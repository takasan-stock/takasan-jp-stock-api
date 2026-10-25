import datetime
import unittest

import pandas as pd

from main import app
from deep_fcf_top5 import build_deep_fcf_top5, evaluate_deep_fcf_rows
from entry_opportunity import build_entry_opportunity
from v3_api import _run_me_pipeline
from multiple_expansion_screener import build_daily_screener
from pretrade import evaluate_pretrade
from short_cover import apply_optimizer_condition, build_priority_alerts, evaluate_entry_hunter
from stock_action_status import evaluate_stock_action_status
from stock_priority_board import build_daily_priority_board
from v3_api import (
    _json_safe,
    _records,
    action_status,
    deep_fcf,
    entry,
    me_daily,
    multiple_expansion,
    pre_trade,
    priority_board,
    short_cover,
)
from v3_contracts import (
    ActionStatusRequest,
    DeepFcfRequest,
    EntryRequest,
    MeDailyRequest,
    MultipleExpansionRequest,
    PreTradeRequest,
    PriorityBoardRequest,
    ShortCoverRequest,
)


def deep_fcf_row(**overrides):
    row = {
        "trade_date": "2026-10-09",
        "ticker": "1111",
        "company_name": "Alpha",
        "fcf_ttm": 100.0,
        "fcf_ttm_complete": True,
        "fcf_per_share_3y_cagr_score": 80.0,
        "fcf_per_share_yoy_score": 70.0,
        "cfo_yoy_score": 75.0,
        "roic_score": 80.0,
        "fcf_margin_score": 85.0,
        "cash_conversion_score": 80.0,
        "net_cash_ratio_score": 70.0,
        "dilution_score": 85.0,
        "fcf_margin": 0.18,
        "cash_conversion": 0.95,
        "p_fcf": 14.0,
        "fcf_quality_grade": "B",
        "fcf_basis": "CFO_MINUS_CAPEX",
        "second_wave_state": "RE-WATCH READY",
        "sw_decision": "READY",
    }
    row.update(overrides)
    return row


def me_history(ticker="6857"):
    dates = pd.bdate_range("2025-01-01", periods=180)
    mlp = []
    for index in range(len(dates)):
        if index < 70:
            mlp.append(1.00 + index * 0.004)
        elif index < 120:
            mlp.append(1.28 - (index - 70) * 0.0022)
        else:
            mlp.append(1.17 + (index - 120) * 0.0012)
    return pd.DataFrame({
        "ticker": ticker,
        "trade_date": dates,
        "mlp_c_core": mlp,
        "adj_close": pd.Series(range(len(dates)), dtype=float) * 2 + 1000,
        "topix_close": pd.Series(range(len(dates)), dtype=float) * 0.4 + 2000,
        "fcf_engine_score": 78.0,
        "market_confirm_count": 3,
        "mex_score": 72.0,
        "state_confirmed": "WATCH",
    })


class V3ApiParityTests(unittest.TestCase):
    def test_action_status_matches_canonical_engine(self):
        payload = {
            "portfolio_row": {"売上5y CAGR": "16%", "ステータス": "監視中"},
            "me_row": {
                "fcf_engine_score": 70,
                "fcf_engine_coverage": 0.8,
                "fcf_quality_grade": "B",
                "mex_score": 78,
                "sw_decision": "READY",
                "second_wave_state": "RE-WATCH READY",
                "rs_rating": 82,
            },
            "short_row": {"alert_tier": "🔥 A 優先確認", "alert_score": 80},
            "live_entry": {"status": "ENTRY READY"},
            "today": "2026-10-24",
        }
        direct = evaluate_stock_action_status(
            portfolio_row=payload["portfolio_row"],
            me_row=payload["me_row"],
            short_row=payload["short_row"],
            live_entry=payload["live_entry"],
            today=datetime.date(2026, 10, 24),
        )
        response = action_status(ActionStatusRequest(**payload))
        self.assertEqual(response["data"], _json_safe(direct))
        self.assertEqual(response["data"]["stage"], "PRETRADE")
        self.assertGreaterEqual(response["data"]["readiness_score"], 60)

    def test_unavailable_contract_preserves_null(self):
        response = action_status(ActionStatusRequest())
        self.assertFalse(response["available"])
        self.assertIsNone(response["data"])
        self.assertEqual(response["coverage"], 0.0)
        self.assertNotEqual(response["unavailable_reason"], "")

    def test_all_v3_routes_are_registered_without_replacing_analyze_routes(self):
        paths = {route.path for route in app.routes}
        self.assertTrue({
            "/v3/action-status",
            "/v3/priority-board",
            "/v3/entry",
            "/v3/short-cover",
            "/v3/pre-trade",
            "/v3/deep-fcf",
            "/v3/me",
            "/v3/me-daily",
        }.issubset(paths))
        self.assertIn("/analyze/{ticker}", paths)
        self.assertIn("/analyze/batch", paths)

    def test_priority_board_matches_stage_and_readiness_order(self):
        portfolio = [
            {"ティッカー": "1001", "銘柄名": "A", "ステータス": "監視中", "売上5y CAGR": "16%"},
            {"ティッカー": "1002", "銘柄名": "B", "ステータス": "監視中", "売上5y CAGR": "18%"},
            {"ティッカー": "1003", "銘柄名": "C", "ステータス": "監視中", "売上5y CAGR": "8%"},
        ]
        me_rows = [
            {"ticker": "1001", "mex_score": 74, "sw_decision": "READY", "fcf_engine_score": 60, "fcf_engine_coverage": 0.7, "fcf_quality_grade": "C"},
            {"ticker": "1002", "mex_score": 82, "sw_decision": "READY", "fcf_engine_score": 75, "fcf_engine_coverage": 0.9, "fcf_quality_grade": "A"},
            {"ticker": "1003", "mex_score": 55, "sw_decision": "WATCH", "fcf_engine_score": 55, "fcf_engine_coverage": 0.6, "fcf_quality_grade": "C"},
        ]
        live_rows = [{"ticker": "1002", "status": "ENTRY READY"}]
        direct = build_daily_priority_board(
            portfolio_rows=portfolio,
            me_rows=me_rows,
            live_entry_rows=live_rows,
            today=datetime.date(2026, 10, 24),
        )
        response = priority_board(PriorityBoardRequest(
            portfolio_rows=portfolio,
            me_rows=me_rows,
            live_entry_rows=live_rows,
            today="2026-10-24",
        ))
        self.assertEqual(response["data"], _json_safe(direct))
        self.assertEqual([row["ticker"] for row in response["data"]], ["1002", "1001", "1003"])

    def test_entry_hunter_and_opportunity_match_canonical(self):
        inputs = {
            "prev_close": 1000,
            "prev_high": 1030,
            "session_open": 1020,
            "current_price": 1050,
            "vwap": 1035,
            "opening15_high": 1040,
            "opening15_low": 1015,
            "relvol15": 1.8,
            "minutes_from_open": 25,
        }
        hunter = entry(EntryRequest(mode="hunter", inputs=inputs))
        self.assertEqual(hunter["data"], evaluate_entry_hunter(**inputs))
        candidate = {"source": "SHORT+ME", "source_score": 90, "adaptive_bonus": 3.0}
        snapshot = {"status": "🟢 ENTRY READY", "score": 85}
        opportunity = entry(EntryRequest(
            mode="opportunity",
            candidate=candidate,
            entry_snapshot=snapshot,
        ))
        self.assertEqual(opportunity["data"], build_entry_opportunity(candidate, snapshot))

    def test_short_cover_priority_matches_canonical(self):
        current = [{
            "ticker": "1111", "name": "TEST", "phase": "🔥 COVER EARLY",
            "regime": "🔥 COVER + NEW MONEY", "cover_score": 82,
            "ignition_score": 78, "long_demand_score": 76,
            "short_pressure": 68, "confidence": 82, "vol_ratio": 2.2,
            "rs_watch": 90, "snapshot_date": "2026-09-18",
        }]
        condition = {
            "cover_min": 70, "ignition_min": 60, "long_min": 60,
            "pressure_min": 50, "confidence_min": 40,
            "robustness": "🟢 ROBUST",
        }
        promotions = [{
            "ticker": "1111", "phase_jump": 1, "cover_delta": 12,
            "ignition_delta": 18, "fresh_breakout": True,
            "fresh_avwap_reclaim": True,
            "promotion_reason": "COVER WATCH → COVER EARLY",
        }]
        direct_current = apply_optimizer_condition(pd.DataFrame(current), condition)
        direct = build_priority_alerts(direct_current, pd.DataFrame(promotions), limit=5)
        response = short_cover(ShortCoverRequest(
            current_rows=current,
            promotion_rows=promotions,
            optimizer_condition=condition,
        ))
        self.assertEqual(response["data"], _records(direct))

    def test_pretrade_matches_canonical_without_changing_missing_values(self):
        technical = {
            "data_ok": True,
            "price": 1500.0,
            "sma20": 1450.0,
            "sma50": 1400.0,
            "sma200": 1200.0,
            "stage2": True,
            "rs_proxy": 85.0,
            "volume_ratio": 1.8,
            "atr_pct": 2.0,
        }
        kwargs = {
            "entry": 1500.0, "stop": 1455.0, "target": 1635.0,
            "capital": 1_000_000.0, "risk_percent": 1.0,
            "earnings_days": 20,
            "entry_hunter": {"status": "🟢 ENTRY READY", "score": 85},
            "pattern_flags": {"VCP": True}, "lot_size": 100,
        }
        direct = evaluate_pretrade(technical, **kwargs)
        response = pre_trade(PreTradeRequest(technical=technical, **kwargs))
        self.assertEqual(response["data"], _json_safe(direct))

    def test_deep_fcf_component_scores_and_ranking_match_canonical(self):
        rows = [
            deep_fcf_row(ticker="1111"),
            deep_fcf_row(ticker="2222", fcf_margin_score=95.0, cash_conversion_score=95.0, p_fcf=9.0),
        ]
        frame = pd.DataFrame(rows)
        response = deep_fcf(DeepFcfRequest(rows=rows))
        self.assertEqual(response["data"]["ranking"], _records(build_deep_fcf_top5(frame)))
        self.assertEqual(response["data"]["evaluations"], _records(evaluate_deep_fcf_rows(frame)))
        self.assertEqual([row["ticker"] for row in response["data"]["ranking"]], ["2222", "1111"])

    def test_multiple_expansion_component_scores_match_canonical(self):
        dates = pd.bdate_range("2026-01-01", periods=40)
        market = pd.DataFrame({
            "trade_date": dates,
            "ticker": "6857",
            "company_name": "アドバンテスト",
            "adj_close": [1000 + index * 5 for index in range(len(dates))],
            "market_cap_pti": [1_000_000 + index * 5_000 for index in range(len(dates))],
            "volume": 2_000_000,
            "volume_ma20": 1_500_000,
            "rs_rating": 80,
            "rs_rating_20d_ago": 72,
            "ema20": [970 + index * 5 for index in range(len(dates))],
            "ema20_5d_ago": [945 + index * 5 for index in range(len(dates))],
            "high_52w": [1100 + index * 5 for index in range(len(dates))],
        })
        financial = pd.DataFrame({
            "trade_date": dates,
            "ticker": "6857",
            "fcf_ttm": 50_000,
            "ebitda_ttm": 90_000,
            "net_income_ttm": 45_000,
            "total_debt": 30_000,
            "cash_and_equivalents": 80_000,
            "diluted_shares_ttm": 1_000,
            "fcf_per_share_3y_cagr": 0.20,
            "fcf_per_share_yoy": 0.15,
            "cfo_yoy": 0.18,
            "roic": 0.16,
            "fcf_margin": 0.18,
            "operating_margin": 0.14,
            "cash_conversion": 0.95,
            "net_cash_ratio": 0.12,
            "share_count_1y_change": 0.0,
            "revenue_yoy": 0.12,
            "operating_income_yoy": 0.15,
            "cfo_ttm": 70_000,
            "fcf_basis": "CFO_MINUS_CAPEX",
        })
        config = {"lookback_days": 30, "min_history_days": 10, "core_ema_span": 5}
        direct = _run_me_pipeline(market, financial, config=config)
        response = multiple_expansion(MultipleExpansionRequest(
            market_rows=_records(market),
            financial_rows=_records(financial),
            config=config,
        ))
        self.assertEqual(response["data"], _records(direct))
        self.assertIn("state_confirmed", response["data"][-1])
        self.assertIn("price_driver_label", response["data"][-1])

    def test_me_daily_ranking_matches_canonical(self):
        history = me_history()
        payload = _records(history)
        response = me_daily(MeDailyRequest(history_rows=payload))
        self.assertEqual(response["data"], _records(build_daily_screener(pd.DataFrame(payload))))
        self.assertEqual(response["data"][0]["screen_rank"], 1)


if __name__ == "__main__":
    unittest.main()
