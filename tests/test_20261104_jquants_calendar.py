import unittest
from datetime import date, timedelta
from unittest.mock import Mock

from jquants_client import JQuantsClient
from market_data_normalizer import DataQualityError, normalize_calendar


def day(text):
    return date.fromisoformat(text)


def fixture(start, end, divisions):
    return [{"Date": (start + timedelta(days=i)).isoformat(), "HolDiv": division}
            for i, division in enumerate(divisions)]


class CalendarTests(unittest.TestCase):
    def test_weekend_holiday_ose_only_and_half_session(self):
        start, end = day("2026-01-09"), day("2026-01-14")
        cal = normalize_calendar(fixture(start, end, "100312"), start, end)
        self.assertEqual(cal.next_trading_day(start), day("2026-01-13"))
        self.assertEqual(cal.previous_trading_day(day("2026-01-13")), start)
        self.assertTrue(cal.days[-1].is_trading_day)
        self.assertFalse(cal.days[3].is_trading_day)
        self.assertIsNone(cal.previous_trading_day(start))
        self.assertIsNone(cal.next_trading_day(end))
        self.assertIsNone(cal.next_trading_day(start - timedelta(days=1)))

    def test_year_end_calendar(self):
        start, end = day("2025-12-30"), day("2026-01-06")
        cal = normalize_calendar(fixture(start, end, "10000011"), start, end)
        self.assertEqual(cal.next_trading_day(start), day("2026-01-05"))
        self.assertEqual(cal.previous_trading_day(day("2026-01-05")), start)

    def test_malformed_missing_or_conflicting_calendar(self):
        start, end = day("2026-01-09"), day("2026-01-10")
        rows = fixture(start, end, "10")
        for invalid in ([], rows[:1], [{**rows[0], "HolDiv": "9"}, rows[1]],
                        [{**rows[0], "HolDiv": 1}, rows[1]],
                        rows + [{**rows[0], "HolDiv": "0"}]):
            with self.subTest(invalid=invalid), self.assertRaises(DataQualityError):
                normalize_calendar(invalid, start, end)

    def test_calendar_endpoint_and_cache(self):
        from secrets import token_urlsafe
        start, end = day("2026-01-09"), day("2026-01-10")
        get = Mock(return_value=Mock(status_code=200, json=Mock(
            return_value={"data":fixture(start, end, "10")})))
        client = JQuantsClient(token_urlsafe(), get=get)
        self.assertIs(client.calendar(start, end), client.calendar(start, end))
        self.assertEqual(get.call_count, 1)
        self.assertTrue(get.call_args.args[0].endswith("/v2/markets/calendar"))
        self.assertEqual(get.call_args.kwargs["params"], {"from":"2026-01-09", "to":"2026-01-10"})
