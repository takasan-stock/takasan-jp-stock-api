import unittest

from growth_quality import calculate_growth_quality
from v11_foundation import V11_CONFIG


RETRIEVED_AT = "2026-09-12T00:00:00Z"
PERIODS = [f"{year}-03-31" for year in range(2021, 2027)]


def series(metric, values, periods=None, source="EDINET"):
    selected_periods = periods or PERIODS[-len(values):]
    return {
        "metric": metric,
        "status": "available",
        "source": source,
        "source_field": metric,
        "consolidation": "consolidated",
        "currency": "JPY",
        "retrieved_at": RETRIEVED_AT,
        "points": [{"period": period, "value": value} for period, value in zip(selected_periods, values)],
        "point_count": len(values),
        "missing_reason": None,
    }


def history(revenue=None, eps=None, gross=None, operating=None, ocf=None):
    revenue = revenue or [100, 110, 122, 140, 170, 220]
    eps = eps or [10, 11, 13, 16, 21, 28]
    gross = gross or [40, 45, 51, 60, 75, 101]
    operating = operating or [10, 11, 14, 18, 25, 36]
    ocf = ocf or [12, 14, 18, 22, 30, 42]
    latest = PERIODS[-1]
    return {
        "history_profile": "standard_5y" if len(revenue) >= 6 else "ipo_or_short_history",
        "series": {
            "revenue": series("revenue", revenue),
            "eps": series("eps", eps),
            "gross_profit": series("gross_profit", gross),
            "operating_profit": series("operating_profit", operating),
            "operating_cash_flow": series("operating_cash_flow", ocf),
            "pretax_income": series("pretax_income", [40], [latest]),
            "tax_provision": series("tax_provision", [10], [latest]),
            "total_equity": series("total_equity", [200], [latest]),
            "total_debt": series("total_debt", [80], [latest]),
            "cash_and_equivalents": series("cash_and_equivalents", [30], [latest]),
        },
    }


def calculate(data=None, ticker="6857", per=15.0):
    return calculate_growth_quality(
        ticker,
        data or history(),
        per=per,
        per_source="Yahoo Finance trailingPE",
        retrieved_at=RETRIEVED_AT,
    )


class GrowthQualityGrowthTests(unittest.TestCase):
    def test_complete_6857_returns_all_growth_horizons_and_score(self):
        result = calculate()
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["evaluation_state"], "standard_5y")
        self.assertEqual(result["revenue_growth"]["five_year_cagr"]["status"], "available")
        self.assertEqual(result["revenue_growth"]["three_year_cagr"]["status"], "available")
        self.assertEqual(result["revenue_growth"]["one_year"]["status"], "available")
        self.assertEqual(result["growth_quality_score"]["status"], "available")
        self.assertTrue(result["independent_from_existing_growth_score"])

    def test_accelerating_maintaining_and_decelerating_are_distinct(self):
        accelerating = calculate(history(revenue=[100, 102, 105, 110, 130, 180]))
        maintaining = calculate(history(revenue=[100, 110, 121, 133.1, 146.41, 161.051]))
        decelerating = calculate(history(revenue=[100, 140, 180, 210, 225, 230]))
        self.assertEqual(accelerating["growth_trend"]["value"], "accelerating")
        self.assertEqual(maintaining["growth_trend"]["value"], "maintaining")
        self.assertEqual(decelerating["growth_trend"]["value"], "decelerating")

    def test_cagr_vs_per_keeps_actual_values(self):
        result = calculate(per=14.2)
        comparison = result["cagr_vs_per"]["comparison"]
        self.assertEqual(comparison["status"], "available")
        self.assertAlmostEqual(comparison["per"], 14.2)
        self.assertAlmostEqual(comparison["revenue_cagr_5y_pct"], result["revenue_growth"]["five_year_cagr"]["value"])
        self.assertIsInstance(comparison["value"], bool)

    def test_negative_eps_start_is_not_calculable_not_zero(self):
        result = calculate(history(eps=[-2, 1, 3, 5, 8, 12]))
        eps = result["eps_growth"]["five_year_cagr"]
        self.assertEqual(eps["status"], "not_calculable")
        self.assertIsNone(eps["value"])


class GrowthQualityShortHistoryTests(unittest.TestCase):
    def test_472a_is_ipo_short_history_and_not_scored_as_five_year_company(self):
        short = history(
            revenue=[100, 140],
            eps=[3, 5],
            gross=[40, 60],
            operating=[10, 18],
            ocf=[8, 14],
        )
        result = calculate(short, ticker="472A")
        self.assertEqual(result["ticker"], "472A")
        self.assertEqual(result["status"], "short_history")
        self.assertEqual(result["evaluation_state"], "ipo_short_history")
        self.assertEqual(result["revenue_growth"]["five_year_cagr"]["status"], "short_history")
        self.assertEqual(result["revenue_growth"]["one_year"]["status"], "available")
        self.assertEqual(result["growth_quality_score"]["status"], "short_history")

    def test_no_revenue_data_is_not_acquired(self):
        data = history()
        data["series"]["revenue"] = {"status": "not_acquired", "points": [], "missing_reason": "source_missing"}
        result = calculate(data)
        self.assertEqual(result["status"], "not_acquired")
        self.assertEqual(result["revenue_growth"]["one_year"]["status"], "not_acquired")
        self.assertIsNone(result["revenue_growth"]["one_year"]["value"])


class GrowthQualityQualityComponentTests(unittest.TestCase):
    def test_margin_trends_and_operating_cash_flow_are_traced(self):
        result = calculate()
        gross = result["profitability"]["gross_margin"]
        operating = result["profitability"]["operating_margin"]
        self.assertGreaterEqual(len(gross["series"]), 2)
        self.assertIn(gross["trend"], {"improving", "stable", "deteriorating"})
        self.assertIn(operating["trend"], {"improving", "stable", "deteriorating"})
        self.assertTrue(result["operating_cash_flow"]["positive"])

    def test_roic_uses_same_period_inputs_and_exposes_formula_inputs(self):
        result = calculate()
        roic = result["roic"]
        self.assertEqual(roic["status"], "available")
        self.assertIn("invested_capital", roic["inputs"])
        self.assertIn("effective_tax_rate", roic["inputs"])

    def test_roic_missing_input_is_not_zero(self):
        data = history()
        data["series"]["tax_provision"] = {"status": "not_acquired", "points": [], "missing_reason": "source_missing"}
        result = calculate(data)
        self.assertEqual(result["roic"]["status"], "not_acquired")
        self.assertIsNone(result["roic"]["value"])

    def test_multiple_warning_flags_are_kept_separately_from_score(self):
        data = history(
            revenue=[100, 140, 180, 210, 225, 230],
            gross=[60, 75, 88, 96, 92, 85],
            operating=[25, 32, 38, 41, 35, 20],
            ocf=[30, 28, 24, 18, 5, -4],
        )
        result = calculate(data, per=50.0)
        codes = {flag["code"] for flag in result["warning_flags"]}
        self.assertTrue({"GROWTH_DECELERATING", "GROSS_MARGIN_DETERIORATING", "OPERATING_MARGIN_DETERIORATING", "OPERATING_CASH_FLOW_NON_POSITIVE", "CAGR_NOT_ABOVE_PER"}.issubset(codes))
        self.assertEqual(result["growth_quality_score"]["status"], "available")
        self.assertNotIn("warning_flags", result["growth_quality_score"])

    def test_weights_are_configurable_and_total_one(self):
        weights = V11_CONFIG["growth_quality"]["score"]["weights"]
        self.assertAlmostEqual(sum(weights.values()), 1.0)


if __name__ == "__main__":
    unittest.main()
