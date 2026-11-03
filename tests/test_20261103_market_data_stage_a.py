import io
import logging
import secrets
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from threading import Event
from unittest.mock import Mock

import requests

from jquants_client import JQuantsClient, JQuantsError, LightRateLimiter
from market_data_normalizer import (
    DataQualityError, entry_daily_context, me_market_rows, normalize_daily,
    normalize_financials, pretrade_ohlcv, resolve_instrument,
)


FRIDAY = date(2026, 1, 9)
TUESDAY = date(2026, 1, 13)
MASTER = {"Code": "95600", "Date": FRIDAY.isoformat(), "CoName": "Fixture", "Mkt": "0113"}
BAR = {
    "Code": "95600", "Date": FRIDAY.isoformat(), "O": "100", "H": 105,
    "L": 90, "C": 102, "Vo": "0", "Va": "invalid", "AdjO": 50,
    "AdjH": 52.5, "AdjL": 45, "AdjC": "51", "AdjVo": "", "AdjFactor": "0.5",
}


def page(data=None, cursor=None, status=200, headers=None):
    return Mock(status_code=status, headers=headers or {}, json=Mock(
        return_value={"data": [] if data is None else data, "pagination_key": cursor}
    ))


class Clock:
    def __init__(self):
        self.value = 0
        self.delays = []

    def __call__(self):
        return self.value

    def sleep(self, seconds):
        self.delays.append(seconds)
        self.value += seconds


class StageATests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.get = Mock()
        self.key = secrets.token_urlsafe()
        self.instrument = resolve_instrument("9560", [MASTER], FRIDAY)

    def client(self, **options):
        return JQuantsClient(
            self.key, get=self.get, clock=self.clock, sleep=self.clock.sleep,
            limiter=LightRateLimiter(clock=self.clock, sleep=self.clock.sleep),
            now=lambda: datetime.fromtimestamp(self.clock(), timezone.utc), **options,
        )

    def test_master_codes_effective_date_and_other_share_class(self):
        for requested, code in (("9560", "95600"), ("95600", "95600"),
                                ("472A", "472A0"), ("472A0", "472A0")):
            master = {**MASTER, "Code": code}
            self.get.side_effect = [page([master, {**master, "Date": TUESDAY.isoformat()}])]
            result = self.client().instrument(requested, FRIDAY)
            self.assertEqual((result.requested_ticker, result.jquants_code, result.effective_date),
                             (requested, code, FRIDAY))
        with self.assertRaises(DataQualityError):
            resolve_instrument("9560", [{**MASTER, "Code": "95601"}], FRIDAY)
        with self.assertRaises(DataQualityError):
            resolve_instrument("9560", [{**MASTER, "Date": TUESDAY.isoformat()}], FRIDAY)

    def test_daily_fields_duplicates_split_and_adapters(self):
        instrument = resolve_instrument("472A", [{**MASTER, "Code": "472A0"}], FRIDAY)
        row = {**BAR, "Code": "472A0"}
        bars = normalize_daily([row, dict(row)], instrument)
        self.assertEqual(len(bars), 1)
        bar = bars[0]
        self.assertEqual((bar.open, bar.high, bar.low, bar.close, bar.volume, bar.turnover),
                         (100, 105, 90, 102, 0, None))
        self.assertEqual((bar.adj_open, bar.adj_high, bar.adj_low, bar.adj_close,
                          bar.adj_volume, bar.adjustment_factor), (50, 52.5, 45, 51, None, 0.5))
        self.assertEqual(me_market_rows(bars)[0], {
            "ticker": "472A", "trade_date": FRIDAY.isoformat(), "adj_close": 51})
        self.assertEqual(pretrade_ohlcv(bars)[0]["Close"], 51)
        self.assertEqual(entry_daily_context(bars, TUESDAY, [FRIDAY, TUESDAY]),
                         {"prev_close": 102, "prev_high": 105})
        self.assertIsNone(entry_daily_context(bars, TUESDAY, [])["prev_close"])
        with self.assertRaises(DataQualityError):
            normalize_daily([row, {**row, "AdjC": 52}], instrument)
        with self.assertRaises(DataQualityError):
            normalize_daily([{**row, "Date": "invalid"}], instrument)

    def test_financial_revisions_and_holiday(self):
        row = {"Code": "95600", "DiscDate": FRIDAY.isoformat(), "DiscTime": "16:00:00",
               "DiscNo": "1", "DocType": "FinancialStatements", "CurPerType": "FY",
               "CurPerSt": "2025-01-01", "CurPerEn": "2025-12-31",
               "Sales": "0", "OP": "", "NP": None, "CFO": "123.5"}
        self.get.side_effect = [page([row, {**row, "DiscNo": "2", "DocType": "Revision", "Sales": "20"}])]
        client = self.client()
        original, revision = client.financials(self.instrument, [FRIDAY, TUESDAY])
        self.assertEqual(original.available_date, TUESDAY)
        self.assertEqual(original.disclosure_timestamp.isoformat(), "2026-01-09T16:00:00+09:00")
        self.assertEqual(original.accounting_period, "FY")
        self.assertEqual(original.period_end.isoformat(), "2025-12-31")
        self.assertEqual([original.normalized[k] for k in ("Sales", "OP", "NP", "CFO")],
                         [0, None, None, 123.5])
        self.assertEqual(revision.normalized["Sales"], 20)
        self.assertNotEqual(original.revision_identity, revision.revision_identity)
        self.assertIsNone(client.financials(self.instrument, [FRIDAY])[0].available_date)
        self.assertEqual(self.get.call_count, 1)

    def test_pagination_empty_page_and_cache(self):
        self.get.side_effect = [page([], "next"), page([BAR])]
        client = self.client()
        result = client.daily(self.instrument, FRIDAY, FRIDAY)
        self.assertEqual(result.cache_status, "miss")
        self.assertEqual(result.quality.missing_fields, ("turnover", "adj_volume"))
        params = self.get.call_args.kwargs["params"]
        self.assertEqual(params, {"code": "95600", "from": FRIDAY.isoformat(),
                                  "to": FRIDAY.isoformat(), "pagination_key": "next"})
        self.assertEqual(client.daily(self.instrument, FRIDAY, FRIDAY).cache_status, "hit")
        self.assertEqual(self.get.call_count, 2)
        self.get.side_effect = [page()]
        self.assertEqual(client.daily(self.instrument, TUESDAY, TUESDAY).quality.data_quality, "empty")

    def test_rate_buckets_count_every_page(self):
        self.get.side_effect = [page([], str(i)) for i in range(60)] + [page([BAR])]
        client = self.client()
        client.daily(self.instrument, FRIDAY, FRIDAY)
        self.assertEqual(self.get.call_count, 61)
        self.assertEqual(self.clock.delays, [60])
        for _ in range(60):
            client._limiter.acquire("financial")
        self.assertEqual(self.clock.delays, [60])

    def test_http_retry_timeout_and_invalid_envelopes(self):
        for outcomes, status, delays in (
            ([page(status=429, headers={"Retry-After": "3"}), page(status=429), page(status=429)], 429, [3, 2]),
            ([page(status=502)] * 3, 502, [1, 2]),
            ([requests.Timeout()] * 3, None, [1, 2]),
            ([page(status=403)], 403, []),
            ([Mock(status_code=204, json=Mock(side_effect=ValueError()))], None, []),
            ([Mock(status_code=200, json=Mock(return_value={}))], None, []),
            ([page([], "repeat"), page([], "repeat")], None, []),
        ):
            with self.subTest(status=status, delays=delays):
                self.clock = Clock()
                self.get.reset_mock()
                self.get.side_effect = outcomes
                with self.assertRaises(JQuantsError) as error:
                    self.client().daily(self.instrument, FRIDAY, FRIDAY)
                self.assertEqual(error.exception.status, status)
                self.assertEqual(self.clock.delays, delays)
                self.assertEqual(self.get.call_count, len(outcomes))

    def test_stale_cache_age_and_fetch_timestamp(self):
        self.get.side_effect = [page([BAR]), page(status=503), page(status=503)]
        client = self.client(ttl=10, stale_ttl=20, retries=0)
        initial = client.daily(self.instrument, FRIDAY, FRIDAY)
        self.clock.value = 11
        stale = client.daily(self.instrument, FRIDAY, FRIDAY)
        self.assertTrue(stale.quality.stale)
        self.assertEqual(stale.quality.fetched_at, initial.quality.fetched_at)
        self.clock.value = 20
        with self.assertRaises(JQuantsError):
            client.daily(self.instrument, FRIDAY, FRIDAY)

    def test_concurrent_requests_share_fetch(self):
        entered, release = Event(), Event()

        def fetch(*args, **kwargs):
            entered.set()
            if not release.wait(3):
                raise AssertionError("Timed out waiting for concurrent fetch test")
            return page([BAR])

        self.get.side_effect = fetch
        client = self.client()
        with ThreadPoolExecutor(2) as executor:
            first = executor.submit(client.daily, self.instrument, FRIDAY, FRIDAY)
            self.assertTrue(entered.wait(3))
            second = executor.submit(client.daily, self.instrument, FRIDAY, FRIDAY)
            release.set()
            self.assertEqual(first.result().bars, second.result().bars)
        self.assertEqual(self.get.call_count, 1)

    def test_key_only_in_header_and_no_error_leak(self):
        self.get.side_effect = requests.ConnectionError(self.key)
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logging.getLogger().addHandler(handler)
        try:
            with self.assertRaises(JQuantsError) as error:
                self.client(retries=0).daily(self.instrument, FRIDAY, FRIDAY)
        finally:
            logging.getLogger().removeHandler(handler)
        self.assertNotIn(self.key, stream.getvalue() + str(error.exception))
        self.assertEqual(self.get.call_args.kwargs["headers"], {"x-api-key": self.key})
        self.assertNotIn(self.key, self.get.call_args.args[0])
        self.assertNotIn(self.key, str(self.get.call_args.kwargs["params"]))
        self.assertFalse(self.get.call_args.kwargs["allow_redirects"])
