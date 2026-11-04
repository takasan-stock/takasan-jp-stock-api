import os
import unittest
from datetime import date, datetime, timedelta
from secrets import token_urlsafe
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException

import jquants_pretrade_api as api
from main import app
from market_data_normalizer import JST, normalize_calendar
from market_data_types import DailyBar


class ProtectedPreTradeTests(unittest.TestCase):
    def test_auth_failure_never_starts_jquants(self):
        route = next(route for route in app.routes if route.path == "/internal/v3/pre-trade")
        authenticate = route.dependencies[0].dependency
        token = token_urlsafe()
        with patch.object(api, "_client") as client:
            for configured, supplied, expected in (
                (token, None, 401), (token, "Bearer " + token + "x", 401),
                ("", "Bearer " + token, 503),
            ):
                with patch.dict(os.environ, {"SITES_PRETRADE_SERVICE_TOKEN": configured}):
                    with self.assertRaises(HTTPException) as rejected:
                        authenticate(supplied)
                    self.assertEqual(rejected.exception.status_code, expected)
            client.assert_not_called()

    def test_authentication_and_route_isolation(self):
        route = next(route for route in app.routes if route.path == "/internal/v3/pre-trade")
        self.assertIs(route.dependencies[0].dependency, api.require_sites_service)
        self.assertFalse(route.include_in_schema)
        self.assertIn("/v3/pre-trade", app.openapi()["paths"])
        token = token_urlsafe()
        with patch.dict(os.environ, {"SITES_PRETRADE_SERVICE_TOKEN": ""}):
            with self.assertRaises(HTTPException) as unavailable:
                api.require_sites_service(None)
            self.assertEqual(unavailable.exception.status_code, 503)
        with patch.dict(os.environ, {"SITES_PRETRADE_SERVICE_TOKEN": token}):
            for bad in (None, token, "Bearer " + token + "x"):
                with self.assertRaises(HTTPException) as rejected:
                    api.require_sites_service(bad)
                self.assertEqual(rejected.exception.status_code, 401)
                self.assertNotIn(token, str(rejected.exception.detail))
            api.require_sites_service("Bearer " + token)

    def test_daily_to_existing_evaluator_and_insufficient_input(self):
        now = datetime(2026, 11, 4, 17, tzinfo=JST)
        end = now.date()
        start = end - timedelta(days=450)
        dates = [start + timedelta(days=i) for i in range(452)]
        calendar = normalize_calendar([{"Date": day.isoformat(), "HolDiv": "1" if day.weekday() < 5 else "0"}
                                       for day in dates], start, dates[-1])
        bars = tuple(DailyBar("9560", day, 100+i, 102+i, 99+i, 101+i, 100, 10100,
                              100+i, 102+i, 99+i, 101+i, 100, 1)
                     for i, day in enumerate(calendar.trading_dates) if day <= end)
        client = Mock()
        client.calendar.return_value = calendar
        client.daily.return_value = SimpleNamespace(bars=bars, quality=SimpleNamespace(stale=False))
        request = api.JQuantsPreTradeRequest(ticker="9560", technical={"data_ok": False})
        with patch.object(api, "_client", return_value=client), patch.object(api, "_now", return_value=now):
            result = api.jquants_pretrade(request)
            self.assertTrue(result["available"])
            self.assertEqual(result["coverage"], 0)
            self.assertEqual(result["data_quality"]["status"], "partial")
            self.assertIsNone(result["unavailable_reason"])
            self.assertIn("TRADE BLOCKED", result["data"]["verdict"])
            self.assertIsNone(result["data"]["rr"])
            self.assertTrue(any("RS Proxy" in risk for risk in result["data"]["risks"]))
            client.instrument.assert_called_once_with("9560", end)
            planned = api.jquants_pretrade(api.JQuantsPreTradeRequest(
                ticker="9560", entry=400, stop=380, target=450, capital=1_000_000,
            ))
            self.assertTrue(planned["available"])
            self.assertEqual(planned["coverage"], 1)
            self.assertEqual(planned["data_quality"]["status"], "complete")
            self.assertEqual(planned["data"]["rr"], 2.5)
            self.assertEqual(planned["data"]["position"]["shares"], 500)
            self.assertFalse(planned["data"]["blocked"])
            client.daily.return_value = SimpleNamespace(bars=bars[:-1], quality=SimpleNamespace(stale=False))
            result = api.jquants_pretrade(request)
            self.assertFalse(result["available"])
            self.assertIn("insufficient_input", result["unavailable_reason"])



    def test_missing_api_key_fails_closed(self):
        with patch.object(api, "_client", return_value=None):
            self.assertFalse(api.jquants_pretrade(api.JQuantsPreTradeRequest(ticker="9560"))["available"])

    def test_prepublication_cutoff_uses_previous_confirmed_day(self):
        client = Mock()
        client.calendar.return_value.previous_trading_day.return_value = None
        with patch.object(api, "_client", return_value=client), patch.object(
            api, "_now", return_value=datetime(2026, 11, 4, 16, 29, tzinfo=JST)
        ):
            result = api.jquants_pretrade(api.JQuantsPreTradeRequest(ticker="9560"))
            self.assertFalse(result["available"])
            client.calendar.return_value.previous_trading_day.assert_called_once_with(date(2026, 11, 4))
            client.daily.assert_not_called()

    def test_calendar_failure_does_not_request_daily(self):
        client = Mock()
        client.calendar.side_effect = api.DataQualityError("fixture")
        with patch.object(api, "_client", return_value=client):
            result = api.jquants_pretrade(api.JQuantsPreTradeRequest(ticker="9560"))
            self.assertIn("insufficient_input", result["unavailable_reason"])
            client.daily.assert_not_called()

    def test_stale_data_does_not_reach_evaluator(self):
        client = Mock()
        client.calendar.return_value.previous_trading_day.return_value = date(2026, 11, 4)
        client.daily.return_value.quality.stale = True
        with patch.object(api, "_client", return_value=client), patch.object(api, "pre_trade") as evaluate:
            result = api.jquants_pretrade(api.JQuantsPreTradeRequest(ticker="9560"))
            self.assertFalse(result["available"])
            evaluate.assert_not_called()

    def test_invalid_ticker_cannot_enter_route(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            api.JQuantsPreTradeRequest(ticker="95600")

    def test_public_contract_remains_available_without_secrets(self):
        from v3_contracts import PreTradeRequest
        self.assertFalse(api.pre_trade(PreTradeRequest())["available"])
