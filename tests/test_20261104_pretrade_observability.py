import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from secrets import token_urlsafe
from threading import Barrier
from unittest.mock import Mock, patch

import jquants_pretrade_api as api
from jquants_client import JQuantsClient, JQuantsError, JQuantsRequestMetrics, request_metrics


def reply(status, data=None):
    return Mock(status_code=status, headers={}, json=Mock(return_value={"data": data}))


class ObservabilityTests(unittest.TestCase):
    def test_only_dedicated_key_can_start_client(self):
        key = token_urlsafe()
        legacy = token_urlsafe()
        api._client.cache_clear()
        self.addCleanup(api._client.cache_clear)
        with patch.dict(os.environ, {"JQUANTS_API_KEY": legacy}, clear=True), patch.object(api, "JQuantsClient") as factory:
            self.assertIsNone(api._client())
            factory.assert_not_called()
            api._client.cache_clear()
            os.environ["JQUANTS_PRETRADE_API_KEY"] = key
            self.assertIs(api._client(), factory.return_value)
            factory.assert_called_once_with(key)

    def test_counters_for_retry_429_and_cache_hit(self):
        day = date(2026, 11, 4)
        get = Mock(side_effect=[reply(429), reply(200, [{"Date": day.isoformat(), "HolDiv": "1"}])])
        client = JQuantsClient(token_urlsafe(), get=get, sleep=lambda _: None)
        metrics = JQuantsRequestMetrics()
        context = request_metrics.set(metrics)
        try:
            self.assertIs(client.calendar(day, day), client.calendar(day, day))
        finally:
            request_metrics.reset(context)
        self.assertEqual((metrics.logical_requests, metrics.http_attempts, metrics.cache_hits,
                          metrics.cache_misses, metrics.retries, metrics.rate_limited), (2, 2, 1, 1, 1, 1))

    def test_concurrent_evaluations_share_client_without_mixing_counters(self):
        barrier = Barrier(2, timeout=3)

        def get(_url, *, params, **_kwargs):
            barrier.wait()
            return reply(200, [{"Date": params["from"], "HolDiv": "1"}])

        client = JQuantsClient(token_urlsafe(), get=get)

        def read(day, repeat):
            metrics = JQuantsRequestMetrics()
            context = request_metrics.set(metrics)
            try:
                for _ in range(repeat):
                    client.calendar(day, day)
                return metrics
            finally:
                request_metrics.reset(context)

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(read, date(2026, 11, 4), 2)
            second = executor.submit(read, date(2026, 11, 5), 1)
            a, b = first.result(), second.result()
        self.assertEqual((a.logical_requests, a.http_attempts, a.cache_hits), (2, 1, 1))
        self.assertEqual((b.logical_requests, b.http_attempts, b.cache_hits), (1, 1, 0))
        self.assertIsNone(request_metrics.get())

    def test_correlated_logs_omit_upstream_error_text_and_secret(self):
        sensitive = token_urlsafe()
        client = Mock()
        client.calendar.side_effect = JQuantsError(sensitive, 429)
        with patch.object(api, "_client", return_value=client), self.assertLogs(api.logger, level="INFO") as logs:
            result = api.jquants_pretrade(api.JQuantsPreTradeRequest(ticker="9560"))
            api.jquants_pretrade(api.JQuantsPreTradeRequest(ticker="9560"))
        self.assertFalse(result["available"])
        self.assertNotIn(sensitive, str(result) + "\n".join(logs.output))
        start, end, next_start, _ = [json.loads(record.getMessage()) for record in logs.records]
        self.assertEqual((start["event"], end["event"]), ("pretrade_start", "pretrade_end"))
        self.assertRegex(start["correlation_id"], r"^[a-f0-9]{32}$")
        self.assertEqual(start["correlation_id"], end["correlation_id"])
        self.assertNotEqual(start["correlation_id"], next_start["correlation_id"])
        self.assertEqual((end["upstream_status_class"], end["classification"]), ("4xx", "insufficient_input"))
        self.assertNotIn("correlation_id", result)
        self.assertIsNone(request_metrics.get())
