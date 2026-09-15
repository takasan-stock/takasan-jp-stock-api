import unittest

from coverage_v11 import build_data_coverage
from v11_foundation import V11_CONFIG


PERIOD = "2026-03-31"
RETRIEVED_AT = "2026-09-13T00:00:00Z"


def metric(value=1.0, status="available", source="Yahoo Finance", period=PERIOD, consolidation="source_reported", reason=None, points=6):
    return {
        "value": value,
        "status": status,
        "source": source,
        "fiscal_period": period,
        "consolidation": consolidation,
        "missing_reason": reason,
        "periods": [f"FY-{index}" for index in range(points)],
    }


def complete_payload(roic=20.0):
    metric_values = {
        "revenue_growth_pct": 10.0,
        "operating_profit_growth_pct": 12.0,
        "eps_growth_pct": 11.0,
        "operating_margin_pct": 18.0,
        "free_cash_flow": 100.0,
        "net_debt": 20.0,
        "shares_outstanding": 10.0,
        "market_price": 1000.0,
    }
    lineage = {
        key: metric(value, period=None if key == "market_price" else PERIOD, consolidation=None if key == "market_price" else "source_reported")
        for key, value in metric_values.items()
    }
    for key in ("price_return_5d_pct", "price_return_20d_pct", "volume_ratio_20d", "rs_63d_pct", "rs_126d_pct"):
        lineage[key] = metric(1.0, period=None, consolidation=None)
    raw = {
        key: metric(1.0, period=PERIOD if key not in {"market_cap", "per"} else None, consolidation="source_reported" if key in {"current_assets", "inventory", "investment_securities", "total_liabilities"} else None)
        for key in ("current_assets", "inventory", "investment_securities", "total_liabilities", "market_cap", "per", "roic")
    }
    cash = {
        "raw_inputs": raw,
        "net_cash": metric(),
        "ncr": {"display": metric()},
        "per_cn": metric(),
        "dpup": metric(),
    }
    growth = {
        "revenue_growth": {
            "five_year_cagr": metric(15.0),
            "three_year_cagr": metric(17.0, points=4),
            "one_year": metric(20.0, points=2),
        },
        "eps_growth": {"five_year_cagr": metric(16.0)},
        "profitability": {"gross_margin": metric(40.0, points=2), "operating_margin": metric(18.0, points=2)},
        "operating_cash_flow": metric(100.0, points=1),
        "roic": metric(roic, points=1),
        "growth_trend": metric("accelerating"),
    }
    financial_history = {
        "series": {
            "revenue": {"status": "available", "source": "Yahoo Finance", "consolidation": "source_reported", "points": [{"period": PERIOD, "value": 1.0}]}
        }
    }
    market = {key: 1.0 for key in ("price_return_5d_pct", "price_return_20d_pct", "volume_ratio_20d", "rs_63d_pct", "rs_126d_pct")}
    return {
        "ticker": "6857",
        "fiscal_period": PERIOD,
        "financial_history": financial_history,
        "metrics": metric_values,
        "metric_lineage": lineage,
        "cash_value": cash,
        "growth_quality": growth,
        "market_momentum": market,
        "retrieved_at": RETRIEVED_AT,
    }


def calculate(**changes):
    payload = complete_payload()
    payload.update(changes)
    return build_data_coverage(**payload)


class CoverageCalculationTests(unittest.TestCase):
    def test_complete_payload_returns_six_module_coverages(self):
        result = calculate()
        for key in (
            "core_financial_coverage", "cash_value_coverage", "growth_quality_coverage",
            "market_coverage", "tdnet_coverage", "overall_coverage",
        ):
            self.assertIn(key, result)
        self.assertEqual(result["core_financial_coverage"]["numerator"], 7)
        self.assertEqual(result["core_financial_coverage"]["denominator"], 7)
        self.assertEqual(result["overall_coverage"]["percentage"], 100.0)
        self.assertEqual(result["overall_coverage"]["level"], "high")

    def test_tdnet_not_applicable_is_excluded_from_denominator(self):
        result = calculate()
        tdnet = result["tdnet_coverage"]
        self.assertIsNone(tdnet["percentage"])
        self.assertEqual(tdnet["denominator"], 0)
        self.assertEqual(tdnet["excluded_not_applicable"], 4)
        self.assertEqual(result["overall_coverage"]["denominator"], 33)

    def test_not_acquired_not_calculable_and_short_history_remain_in_denominator(self):
        payload = complete_payload()
        payload["cash_value"]["raw_inputs"]["per"] = metric(None, "not_acquired", reason="per_missing", period=None, consolidation=None)
        payload["cash_value"]["net_cash"] = metric(None, "not_calculable", reason="input_mismatch")
        payload["growth_quality"]["revenue_growth"]["five_year_cagr"] = metric(None, "short_history", reason="requires_6_points")
        result = build_data_coverage(**payload)
        self.assertEqual(result["cash_value_coverage"]["denominator"], 11)
        self.assertEqual(result["cash_value_coverage"]["status_counts"]["not_acquired"], 1)
        self.assertEqual(result["cash_value_coverage"]["status_counts"]["not_calculable"], 1)
        self.assertEqual(result["growth_quality_coverage"]["status_counts"]["short_history"], 1)
        reasons = result["growth_quality_coverage"]["unavailable_reasons"]
        self.assertIn("requires_6_points", {item["reason"] for item in reasons})

    def test_source_period_and_consolidation_checks_can_block_coverage(self):
        payload = complete_payload()
        payload["metric_lineage"]["revenue_growth_pct"]["source"] = None
        payload["metric_lineage"]["eps_growth_pct"]["fiscal_period"] = "2025-03-31"
        payload["metric_lineage"]["operating_margin_pct"]["consolidation"] = "non_consolidated"
        result = build_data_coverage(**payload)
        items = {item["key"]: item for item in result["core_financial_coverage"]["items"]}
        self.assertEqual(items["revenue_growth_pct"]["status"], "not_acquired")
        self.assertEqual(items["eps_growth_pct"]["status"], "not_calculable")
        self.assertEqual(items["operating_margin_pct"]["status"], "not_calculable")

    def test_classification_readiness_checks_required_items_even_when_overall_is_high(self):
        payload = complete_payload()
        payload["growth_quality"]["revenue_growth"]["five_year_cagr"] = metric(None, "short_history", reason="requires_6_points")
        result = build_data_coverage(**payload)
        self.assertGreaterEqual(result["overall_coverage"]["percentage"], 90.0)
        readiness = result["classification_readiness"]["early_candidate"]
        self.assertEqual(readiness["status"], "insufficient_data")
        self.assertIn("growth_quality.revenue_cagr_5y", readiness["missing_required_items"])


class CoverageThresholdAndSanityTests(unittest.TestCase):
    def test_thresholds_are_configurable(self):
        thresholds = V11_CONFIG["coverage"]["confidence_thresholds"]
        self.assertEqual(thresholds, {"high_min_pct": 90.0, "medium_min_pct": 70.0})

    def test_high_roic_review_does_not_change_coverage(self):
        baseline = calculate()
        payload = complete_payload(roic=75.0)
        result = build_data_coverage(**payload)
        self.assertEqual(result["sanity_flags"][0]["code"], "high_roic_review")
        self.assertFalse(result["sanity_flags"][0]["affects_coverage"])
        self.assertFalse(result["sanity_flags"][0]["affects_score"])
        self.assertEqual(result["overall_coverage"]["percentage"], baseline["overall_coverage"]["percentage"])

    def test_extreme_roic_review_uses_higher_threshold(self):
        payload = complete_payload(roic=120.0)
        result = build_data_coverage(**payload)
        self.assertEqual([flag["code"] for flag in result["sanity_flags"]], ["extreme_roic_review"])


if __name__ == "__main__":
    unittest.main()
