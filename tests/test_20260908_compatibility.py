import unittest
from copy import deepcopy
from unittest.mock import patch

import main
from models import ClassificationTrace, ConditionEvidence, MetricEnvelope
from v11_foundation import V11_CONFIG, get_v11_public_metadata


LEGACY_ANALYSIS_KEYS = {
    "ticker",
    "company_name",
    "fiscal_period",
    "comparison_period",
    "data_status",
    "data_source",
    "data_retrieved_at_utc",
    "data_coverage_pct",
    "missing_fields",
    "market_price",
    "forecast_revision",
    "metrics",
    "growth",
    "change",
    "momentum",
    "dcf",
    "mispricing",
    "ai_score",
    "rank",
    "score_weights",
    "quality_flags",
    "note",
}


def snapshot(ticker: str) -> dict:
    forecast = {
        "available": True,
        "status": "available",
        "guidance_revision_pct": 5.0,
        "is_upward_revision": True,
        "is_downward_revision": False,
    }
    metrics = {
        "revenue_growth_pct": 12.0,
        "operating_profit_growth_pct": 15.0,
        "eps_growth_pct": 11.0,
        "operating_margin_pct": 18.0,
        "latest_growth_pct": 12.0,
        "previous_growth_pct": 8.0,
        "margin_change_points": 1.5,
        "guidance_revision_pct": 5.0,
        "guidance_revision_available": True,
        "tdnet_revision_available": False,
        "jquants_revision_available": True,
        "sign_flip_penalty": False,
        "free_cash_flow": 100_000_000.0,
        "net_debt": 10_000_000.0,
        "shares_outstanding": 1_000_000.0,
        "price_return_5d_pct": 2.0,
        "price_return_20d_pct": 8.0,
        "volume_ratio_20d": 1.3,
        "rs_63d_pct": 5.0,
        "rs_126d_pct": 7.0,
    }
    return {
        "ticker": ticker,
        "company_name": f"TEST-{ticker}",
        "fiscal_period": "2026-03-31",
        "comparison_period": {"latest": "2026-03-31", "previous": "2025-03-31", "older": "2024-03-31"},
        "data_status": "test_fixture",
        "data_source": "test_fixture",
        "data_retrieved_at_utc": "2026-09-08T00:00:00Z",
        "data_coverage_pct": 100.0,
        "missing_fields": [],
        "market_price": 1000.0,
        "forecast_revision": forecast,
        "cash_value_v1_1": {
            "status": "available",
            "cash_value_score": {"value": 75.0, "status": "available"},
            "independent_from_existing_scores": True,
        },
        "growth_quality_v1_1": {
            "status": "available",
            "growth_quality_score": {"value": 68.0, "status": "available"},
            "warning_flags": [],
            "independent_from_existing_growth_score": True,
        },
        "coverage_v1_1": {
            "overall_coverage": {"percentage": 88.0},
            "sanity_flags": [],
            "independent_from_existing_data_coverage_pct": True,
            "independent_from_existing_scores": True,
        },
        "metrics": metrics,
    }


class FoundationConfigTests(unittest.TestCase):
    def test_only_completed_steps_are_enabled_and_tdnet_fetch_remains_disabled(self):
        self.assertTrue(V11_CONFIG["feature_flags"])
        self.assertTrue(V11_CONFIG["feature_flags"]["financial_history_lineage"])
        self.assertTrue(V11_CONFIG["feature_flags"]["tdnet_metadata"])
        self.assertTrue(V11_CONFIG["feature_flags"]["cash_value"])
        self.assertTrue(V11_CONFIG["feature_flags"]["growth_quality"])
        self.assertTrue(V11_CONFIG["feature_flags"]["coverage_v1_1"])
        self.assertFalse(V11_CONFIG["feature_flags"]["tdnet_auto_fetch"])
        self.assertTrue(all(
            value is False
            for key, value in V11_CONFIG["feature_flags"].items()
            if key not in {"financial_history_lineage", "tdnet_metadata", "cash_value", "growth_quality", "coverage_v1_1", "automatic_classification", "ranking_ui_v1_1"}
        ))

    def test_balanced_cash_value_weights_total_one(self):
        weights = V11_CONFIG["cash_value_score"]["profiles"]["balanced"]
        self.assertAlmostEqual(sum(weights.values()), 1.0)
        self.assertEqual(V11_CONFIG["cash_value_score"]["active_profile"], "balanced")

    def test_data_source_priority_is_fixed(self):
        priorities = V11_CONFIG["data_source_policy"]["priorities"]
        self.assertEqual(
            priorities["long_term_financial_history"],
            ["EDINET", "J-Quants financial details", "Yahoo Finance"],
        )
        self.assertEqual(
            priorities["latest_company_forecast"],
            ["J-Quants financial summary", "TDnet", "Yahoo Finance"],
        )
        self.assertEqual(
            priorities["price_volume_market_cap"],
            ["Yahoo Finance", "J-Quants", "derived official financial data"],
        )

    def test_classification_trace_contract(self):
        evidence = ConditionEvidence(condition="growth_high", actual_value=80, threshold=75)
        trace = ClassificationTrace(state="matched", matched_conditions=[evidence])
        payload = trace.model_dump()
        self.assertIn("matched_conditions", payload)
        self.assertIn("failed_conditions", payload)
        self.assertIn("missing_conditions", payload)

    def test_metric_missing_is_not_zero(self):
        metric = MetricEnvelope(status="not_acquired", missing_reason="source unavailable")
        self.assertIsNone(metric.value)


class ExistingApiCompatibilityTests(unittest.TestCase):
    def test_individual_analysis_accepts_6857_and_472a(self):
        with patch.object(main, "get_company_snapshot", side_effect=snapshot):
            for raw, expected in (("6857", "6857"), ("472a", "472A")):
                result = main._analyze_one(raw)
                self.assertEqual(result["ticker"], expected)
                self.assertTrue(LEGACY_ANALYSIS_KEYS.issubset(result.keys()))
                self.assertEqual(result["v1_1"]["status"], "step_7_complete")
                self.assertTrue(result["cash_value_v1_1"]["independent_from_existing_scores"])
                self.assertTrue(result["growth_quality_v1_1"]["independent_from_existing_growth_score"])
                self.assertTrue(result["coverage_v1_1"]["independent_from_existing_data_coverage_pct"])
                self.assertTrue(result["classification_v1_1"]["independent_from_existing_scores"])

    def test_batch_analysis_keeps_legacy_contract_and_both_tickers(self):
        with patch.object(main, "get_company_snapshot", side_effect=snapshot):
            result = main.analyze_batch(main.BatchAnalyzeRequest(tickers=["6857", "472a"], sort_by="momentum"))
        self.assertEqual(result["version"], "1.0.0")
        self.assertEqual(result["success_count"], 2)
        self.assertEqual(result["error_count"], 0)
        self.assertEqual({row["ticker"] for row in result["results"]}, {"6857", "472A"})
        self.assertIn("v1_1", result)
        self.assertTrue(all("classification_v1_1" in row for row in result["results"]))

    def test_public_metadata_is_additive_and_tdnet_off(self):
        metadata = get_v11_public_metadata()
        self.assertEqual(metadata["rule_version"], "1.1.0")
        self.assertFalse(metadata["feature_flags"]["tdnet_auto_fetch"])

    def test_cash_value_response_does_not_change_existing_scores(self):
        low = snapshot("6857")
        high = deepcopy(low)
        low["cash_value_v1_1"]["cash_value_score"]["value"] = 0.0
        high["cash_value_v1_1"]["cash_value_score"]["value"] = 100.0
        with patch.object(main, "get_company_snapshot", return_value=low):
            low_result = main._analyze_one("6857")
        with patch.object(main, "get_company_snapshot", return_value=high):
            high_result = main._analyze_one("6857")
        self.assertEqual(low_result["ai_score"], high_result["ai_score"])
        self.assertEqual(low_result["rank"], high_result["rank"])
        self.assertNotEqual(
            low_result["cash_value_v1_1"]["cash_value_score"]["value"],
            high_result["cash_value_v1_1"]["cash_value_score"]["value"],
        )

    def test_growth_quality_response_does_not_change_existing_growth_score(self):
        low = snapshot("6857")
        high = deepcopy(low)
        low["growth_quality_v1_1"]["growth_quality_score"]["value"] = 0.0
        high["growth_quality_v1_1"]["growth_quality_score"]["value"] = 100.0
        high["growth_quality_v1_1"]["warning_flags"] = [{"code": "TEST_WARNING"}]
        with patch.object(main, "get_company_snapshot", return_value=low):
            low_result = main._analyze_one("6857")
        with patch.object(main, "get_company_snapshot", return_value=high):
            high_result = main._analyze_one("6857")
        self.assertEqual(low_result["growth"], high_result["growth"])
        self.assertEqual(low_result["ai_score"], high_result["ai_score"])
        self.assertNotEqual(
            low_result["growth_quality_v1_1"]["growth_quality_score"]["value"],
            high_result["growth_quality_v1_1"]["growth_quality_score"]["value"],
        )

    def test_coverage_v11_does_not_change_legacy_coverage_or_scores(self):
        low = snapshot("6857")
        high = deepcopy(low)
        low["coverage_v1_1"]["overall_coverage"]["percentage"] = 0.0
        high["coverage_v1_1"]["overall_coverage"]["percentage"] = 100.0
        high["coverage_v1_1"]["sanity_flags"] = [{"code": "extreme_roic_review"}]
        with patch.object(main, "get_company_snapshot", return_value=low):
            low_result = main._analyze_one("6857")
        with patch.object(main, "get_company_snapshot", return_value=high):
            high_result = main._analyze_one("6857")
        self.assertEqual(low_result["data_coverage_pct"], high_result["data_coverage_pct"])
        self.assertEqual(low_result["growth"], high_result["growth"])
        self.assertEqual(low_result["ai_score"], high_result["ai_score"])
        self.assertNotEqual(
            low_result["coverage_v1_1"]["overall_coverage"]["percentage"],
            high_result["coverage_v1_1"]["overall_coverage"]["percentage"],
        )

    def test_classification_response_does_not_change_existing_scores(self):
        payload = snapshot("6857")
        with patch.object(main, "get_company_snapshot", return_value=payload), patch.object(
            main,
            "classify_v11",
            return_value={"primary_candidate": {"key": "main_candidate"}, "independent_from_existing_scores": True},
        ):
            selected = main._analyze_one("6857")
        with patch.object(main, "get_company_snapshot", return_value=payload), patch.object(
            main,
            "classify_v11",
            return_value={"primary_candidate": {"key": None}, "independent_from_existing_scores": True},
        ):
            unselected = main._analyze_one("6857")
        for key in ("growth", "change", "momentum", "mispricing", "dcf", "ai_score", "rank"):
            self.assertEqual(selected[key], unselected[key])


if __name__ == "__main__":
    unittest.main()
