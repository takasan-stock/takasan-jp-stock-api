import unittest
from copy import deepcopy

from cash_value import FORMULAS, build_cash_value_inputs, calculate_cash_value, metric
from v11_foundation import V11_CONFIG


PERIOD = "2026-03-31"
RETRIEVED_AT = "2026-09-09T00:00:00Z"


def verified(value: float, **metadata) -> dict:
    return metric(
        value,
        source=metadata.pop("source", "EDINET"),
        fiscal_period=metadata.pop("fiscal_period", PERIOD),
        consolidation=metadata.pop("consolidation", "consolidated"),
        currency=metadata.pop("currency", "JPY"),
        retrieved_at=RETRIEVED_AT,
        **metadata,
    )


def complete_inputs() -> dict[str, dict]:
    return {
        "current_assets": verified(500.0),
        "inventory": verified(50.0),
        "investment_securities": verified(
            100.0,
            cash_value_eligible=True,
            balance_sheet_scope="non_current_assets",
            non_overlapping_with_current_assets=True,
        ),
        "total_liabilities": verified(300.0),
        "market_cap": metric(1000.0, source="Yahoo Finance marketCap", retrieved_at=RETRIEVED_AT),
        "per": metric(20.0, source="Yahoo Finance trailingPE", retrieved_at=RETRIEVED_AT),
        "roic": verified(15.0),
        "sales_cagr_5y": verified(20.0),
        "operating_cash_flow": verified(80.0),
    }


def calculate(ticker: str = "6857", inputs: dict[str, dict] | None = None, sector: str = "Technology") -> dict:
    return calculate_cash_value(
        ticker,
        inputs or complete_inputs(),
        sector=sector,
        retrieved_at=RETRIEVED_AT,
    )


class CashValueFormulaTests(unittest.TestCase):
    def test_6857_complete_case_returns_full_trace(self):
        result = calculate("6857")
        self.assertEqual(result["status"], "available")
        self.assertAlmostEqual(result["net_cash"]["value"], 255.0)
        self.assertAlmostEqual(result["ncr"]["display"]["value"], 25.5)
        self.assertAlmostEqual(result["ncr"]["calculation"]["value"], 25.5)
        self.assertAlmostEqual(result["per_cn"]["value"], 14.9)
        self.assertAlmostEqual(result["dpup"]["value"], 15.0 / 14.9)
        self.assertEqual(result["formulas"], FORMULAS)
        self.assertEqual(result["cash_value_score"]["profile"], "balanced")
        self.assertFalse(result["score_materials"]["sales_cagr_5y_gt_per"]["raw_value"])
        self.assertTrue(result["independent_from_existing_scores"])

    def test_ncr_above_100_preserves_display_and_caps_calculation_only(self):
        inputs = complete_inputs()
        inputs["current_assets"] = verified(2000.0)
        result = calculate(inputs=inputs)
        self.assertGreater(result["ncr"]["display"]["value"], 100.0)
        self.assertEqual(result["ncr"]["calculation"]["value"], 99.99)
        self.assertTrue(result["ncr"]["cap_applied"])
        self.assertAlmostEqual(result["per_cn"]["value"], 0.002)

    def test_negative_ncr_is_not_floored(self):
        inputs = complete_inputs()
        inputs["total_liabilities"] = verified(800.0)
        result = calculate(inputs=inputs)
        self.assertLess(result["ncr"]["display"]["value"], 0.0)
        self.assertEqual(result["ncr"]["display"]["value"], result["ncr"]["calculation"]["value"])
        self.assertGreater(result["per_cn"]["value"], inputs["per"]["value"])

    def test_negative_roic_is_preserved_but_dpup_uses_zero(self):
        inputs = complete_inputs()
        inputs["roic"] = verified(-8.0)
        result = calculate(inputs=inputs)
        self.assertEqual(result["roic"]["display"]["value"], -8.0)
        self.assertEqual(result["roic"]["dpup_calculation"]["value"], 0.0)
        self.assertTrue(result["roic"]["negative_floor_applied"])
        self.assertEqual(result["dpup"]["value"], 0.0)


class CashValueUnavailableTests(unittest.TestCase):
    def test_472a_possible_investment_overlap_is_not_calculable(self):
        inputs = complete_inputs()
        inputs["investment_securities"]["cash_value_eligible"] = False
        inputs["investment_securities"]["non_overlapping_with_current_assets"] = False
        result = calculate("472A", inputs)
        self.assertEqual(result["ticker"], "472A")
        self.assertEqual(result["net_cash"]["status"], "not_calculable")
        self.assertIn("possible_overlap_with_current_assets", result["net_cash"]["missing_reason"])
        self.assertIsNone(result["ncr"]["display"]["value"])

    def test_per_missing_does_not_become_zero(self):
        inputs = complete_inputs()
        inputs["per"] = metric(None, "not_acquired", source="Yahoo Finance trailingPE", missing_reason="per_missing")
        result = calculate(inputs=inputs)
        self.assertIsNone(result["per_cn"]["value"])
        self.assertEqual(result["per_cn"]["status"], "not_calculable")
        self.assertEqual(result["cash_value_score"]["status"], "insufficient_data")

    def test_investment_securities_without_noncurrent_proof_is_not_calculable(self):
        inputs = complete_inputs()
        inputs["investment_securities"].pop("balance_sheet_scope")
        result = calculate(inputs=inputs)
        self.assertIn("non_current_scope_not_verified", result["net_cash"]["missing_reason"])

    def test_fiscal_period_mismatch_is_not_calculable(self):
        inputs = complete_inputs()
        inputs["total_liabilities"] = verified(300.0, fiscal_period="2025-03-31")
        result = calculate(inputs=inputs)
        self.assertIn("fiscal_period_mismatch", result["net_cash"]["missing_reason"])

    def test_unverified_consolidation_is_not_calculable(self):
        inputs = complete_inputs()
        inputs["inventory"] = verified(50.0, consolidation="source_reported")
        result = calculate(inputs=inputs)
        self.assertIn("consolidation_not_verified_or_mismatch", result["net_cash"]["missing_reason"])

    def test_loss_making_company_with_nonpositive_per_is_not_calculable(self):
        inputs = complete_inputs()
        inputs["per"] = metric(None, "not_calculable", missing_reason="per_not_positive_loss_making_company")
        inputs["operating_cash_flow"] = verified(-30.0)
        result = calculate(inputs=inputs)
        self.assertIsNone(result["per_cn"]["value"])
        self.assertEqual(result["score_materials"]["operating_cash_flow"]["normalized_score"], 0.0)
        self.assertEqual(result["cash_value_score"]["status"], "insufficient_data")

    def test_missing_history_values_remain_missing(self):
        history = {"series": {}}
        inputs = build_cash_value_inputs(
            history,
            fiscal_period=PERIOD,
            market_cap=None,
            per=None,
            retrieved_at=RETRIEVED_AT,
            market_cap_source="Yahoo Finance marketCap",
            per_source="Yahoo Finance trailingPE",
        )
        for name in ("current_assets", "inventory", "investment_securities", "total_liabilities"):
            self.assertIsNone(inputs[name]["value"])
        self.assertIsNone(inputs["market_cap"]["value"])
        self.assertIsNone(inputs["per"]["value"])

    def test_non_primary_material_missing_is_excluded_and_score_remains_available(self):
        inputs = complete_inputs()
        inputs["sales_cagr_5y"] = metric(None, "short_history", missing_reason="requires_6_points")
        result = calculate(inputs=inputs)
        self.assertEqual(result["cash_value_score"]["status"], "available")
        self.assertIn("sales_cagr_5y_gt_per", result["cash_value_score"]["excluded_materials"])


class DpupSectorTests(unittest.TestCase):
    def test_financial_sector_is_not_applicable_and_score_renormalizes(self):
        result = calculate(sector="Financial Services / Banks")
        self.assertEqual(result["dpup"]["status"], "not_applicable")
        self.assertEqual(result["score_materials"]["dpup"]["status"], "not_applicable")
        self.assertEqual(result["cash_value_score"]["status"], "available")
        self.assertAlmostEqual(result["cash_value_score"]["active_weight"], 0.8)

    def test_real_estate_sector_is_not_applicable(self):
        result = calculate(sector="不動産業")
        self.assertEqual(result["dpup"]["status"], "not_applicable")
        self.assertIn("real_estate", result["dpup"]["missing_reason"])


class CashValueConfigurationTests(unittest.TestCase):
    def test_balanced_weights_are_configurable_and_total_one(self):
        profile = V11_CONFIG["cash_value_score"]["profiles"]["balanced"]
        self.assertAlmostEqual(sum(profile.values()), 1.0)
        self.assertEqual(set(profile), set(V11_CONFIG["cash_value_score"]["scoring"]))
        self.assertEqual(V11_CONFIG["cash_value_score"]["calculation"]["ncr_calculation_cap_pct"], 99.99)


if __name__ == "__main__":
    unittest.main()
