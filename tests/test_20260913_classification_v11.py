import unittest

from classification_v11 import _condition, _or_group, classify_v11


def momentum(score=80.0, volume=2.0, rs63=8.0, rs126=6.0, ret5=5.0, ret20=8.0):
    return {
        "score": score,
        "market": {
            "price_return_5d_pct": ret5,
            "price_return_20d_pct": ret20,
            "volume_ratio_20d": volume,
            "rs_63d_pct": rs63,
            "rs_126d_pct": rs126,
        },
    }


def cash(score=80.0, score_status="available", ncr=40.0, dpup=2.0, dpup_status="available", per_cn=12.0):
    return {
        "cash_value_score": {"value": score, "status": score_status},
        "ncr": {"display": {"value": ncr, "status": "available" if ncr is not None else "not_calculable"}},
        "dpup": {"value": dpup, "status": dpup_status},
        "per_cn": {"value": per_cn, "status": "available" if per_cn is not None else "not_calculable"},
    }


def growth_quality(comparison=True, comparison_status="available", warnings=None, score=75.0, per=15.0):
    return {
        "growth_quality_score": {"value": score, "status": "available" if score is not None else "short_history"},
        "cagr_vs_per": {
            "comparison": {"value": comparison, "status": comparison_status, "missing_reason": "short_history" if comparison_status != "available" else None},
            "per": {"value": per, "status": "available" if per is not None else "not_acquired"},
        },
        "warning_flags": warnings or [],
    }


def classify(
    *, growth=75.0, change=75.0, momentum_data=None, mispricing=70.0,
    cash_data=None, quality=None, upward=True, forecast_available=True,
    coverage_pct=95.0, sanity=None,
):
    return classify_v11(
        "6857",
        growth_result={"score": growth} if growth is not None else None,
        change_result={"score": change} if change is not None else None,
        momentum_result=momentum_data or momentum(),
        mispricing_result={"score": mispricing} if mispricing is not None else None,
        forecast_revision={"available": forecast_available, "is_upward_revision": upward, "guidance_revision_pct": 10.0 if upward else -2.0},
        cash_value=cash_data or cash(),
        growth_quality=quality or growth_quality(),
        coverage={"overall_coverage": {"percentage": coverage_pct}, "sanity_flags": sanity or []},
    )


class CandidateClassificationTests(unittest.TestCase):
    def test_main_candidate_all_conditions_match(self):
        result = classify()
        self.assertEqual(result["classifications"]["main_candidate"]["state"], "matched")
        self.assertEqual(result["primary_candidate"]["key"], "main_candidate")

    def test_early_candidate_all_conditions_match(self):
        result = classify(momentum_data=momentum(score=55.0), upward=False)
        self.assertEqual(result["classifications"]["early_candidate"]["state"], "matched")
        self.assertEqual(result["primary_candidate"]["key"], "early_candidate")

    def test_momentum_candidate_all_conditions_match(self):
        result = classify(growth=50.0, change=50.0)
        self.assertEqual(result["classifications"]["momentum_candidate"]["state"], "matched")
        self.assertEqual(result["primary_candidate"]["key"], "momentum_candidate")

    def test_primary_priority_prefers_main_over_momentum(self):
        result = classify()
        self.assertEqual(result["classifications"]["momentum_candidate"]["state"], "matched")
        self.assertEqual(result["primary_candidate"]["key"], "main_candidate")

    def test_required_item_missing_is_insufficient(self):
        result = classify(growth=None)
        trace = result["classifications"]["main_candidate"]
        self.assertEqual(trace["state"], "insufficient_data")
        self.assertIn("growth_score", {item["condition"] for item in trace["missing_conditions"]})

    def test_failed_condition_wins_over_other_missing_conditions(self):
        result = classify(change=55.0, momentum_data=momentum(score=None), upward=False, forecast_available=False, cash_data=cash(score=None, score_status="insufficient_data"))
        self.assertEqual(result["classifications"]["main_candidate"]["state"], "not_matched")

    def test_failed_precedence_applies_to_all_candidate_types(self):
        quality = growth_quality(None, "short_history", score=None)
        result = classify(change=55.0, momentum_data=momentum(score=30.0, volume=None, rs63=None, rs126=None), quality=quality, mispricing=None, upward=False, forecast_available=False)
        self.assertEqual(result["classifications"]["early_candidate"]["state"], "not_matched")
        self.assertEqual(result["classifications"]["momentum_candidate"]["state"], "not_matched")
        self.assertEqual(result["overheat_risk"]["state"], "not_matched")

    def test_high_coverage_does_not_replace_missing_five_year_cagr(self):
        quality = growth_quality(None, "short_history", score=None)
        result = classify(momentum_data=momentum(score=55.0), quality=quality, coverage_pct=100.0, upward=False)
        self.assertEqual(result["classifications"]["early_candidate"]["state"], "insufficient_data")
        self.assertFalse(result["coverage_policy"]["overall_coverage_used_as_investment_score"])

    def test_low_coverage_does_not_block_complete_momentum_conditions(self):
        result = classify(growth=50.0, change=50.0, coverage_pct=20.0)
        self.assertEqual(result["classifications"]["momentum_candidate"]["state"], "matched")
        self.assertEqual(result["primary_candidate"]["key"], "momentum_candidate")

    def test_financial_or_real_estate_dpup_not_applicable_is_missing_for_early_candidate(self):
        for sector in ("financial", "real_estate"):
            with self.subTest(sector=sector):
                result = classify(momentum_data=momentum(score=55.0), cash_data=cash(dpup=None, dpup_status="not_applicable"), upward=False)
                trace = result["classifications"]["early_candidate"]
                self.assertEqual(trace["state"], "insufficient_data")
                self.assertIn("dpup", {item["condition"] for item in trace["missing_conditions"]})

    def test_ipo_short_history_is_not_forced_into_early_candidate(self):
        quality = growth_quality(None, "short_history", score=None)
        result = classify(momentum_data=momentum(score=55.0), quality=quality, upward=False)
        self.assertEqual(result["classifications"]["early_candidate"]["state"], "insufficient_data")

    def test_tdnet_off_does_not_zero_out_available_upward_revision(self):
        result = classify(growth=50.0, change=50.0)
        self.assertEqual(result["classifications"]["momentum_candidate"]["state"], "matched")
        self.assertFalse(result["tdnet_policy"]["auto_fetch_enabled"])
        self.assertTrue(result["tdnet_policy"]["missing_tdnet_not_scored_as_zero"])

    def test_secondary_tags_are_returned_without_primary_candidate(self):
        result = classify(growth=50.0, change=50.0, upward=False, forecast_available=False)
        self.assertIsNone(result["primary_candidate"]["key"])
        self.assertIn("momentum_strong", {item["code"] for item in result["secondary_tags"]})


class TagsAndRiskTests(unittest.TestCase):
    def test_overheat_risk_matches_three_of_four_signals(self):
        result = classify(momentum_data=momentum(score=90.0, ret5=15.0), mispricing=30.0, quality=growth_quality(False, per=35.0))
        self.assertEqual(result["overheat_risk"]["state"], "matched")
        self.assertIn("overheat_review", {item["code"] for item in result["risk_flags"]})

    def test_main_and_overheat_can_coexist(self):
        result = classify(momentum_data=momentum(score=90.0, ret5=15.0), mispricing=30.0, quality=growth_quality(False, per=35.0))
        self.assertEqual(result["primary_candidate"]["key"], "main_candidate")
        self.assertIn("momentum_strong", {item["code"] for item in result["secondary_tags"]})
        self.assertIn("overheat_review", {item["code"] for item in result["risk_flags"]})

    def test_growth_quality_warnings_are_separate_and_multiple_warnings_suppress_early_candidate(self):
        warnings = [
            {"code": "GROWTH_DECELERATING", "evidence": -3.0},
            {"code": "GROSS_MARGIN_DETERIORATING", "evidence": -2.0},
        ]
        result = classify(momentum_data=momentum(score=55.0), quality=growth_quality(warnings=warnings), upward=False)
        self.assertEqual(result["classifications"]["early_candidate"]["state"], "not_matched")
        self.assertEqual({item["code"] for item in result["risk_flags"]}, {"growth_decelerating", "gross_margin_deteriorating"})

    def test_roic_sanity_flag_is_attached_without_affecting_scores(self):
        sanity = [{"code": "high_roic_review", "value_pct": 75.0, "affects_score": False}]
        result = classify(sanity=sanity)
        flag = next(item for item in result["risk_flags"] if item["code"] == "high_roic_review")
        self.assertFalse(flag["affects_score"])
        self.assertEqual(result["primary_candidate"]["key"], "main_candidate")

    def test_every_classification_returns_three_trace_lists(self):
        result = classify()
        for trace in result["classifications"].values():
            self.assertIn("matched_conditions", trace)
            self.assertIn("failed_conditions", trace)
            self.assertIn("missing_conditions", trace)


class OrGroupTests(unittest.TestCase):
    def item(self, key, status):
        return _condition(key, key, status, status == "matched")

    def test_one_matching_alternative_matches_group(self):
        group = _or_group("catalyst", "catalyst", [self.item("a", "matched"), self.item("b", "missing")], "fixture")
        self.assertEqual(group["status"], "matched")

    def test_all_known_failures_fail_group(self):
        group = _or_group("catalyst", "catalyst", [self.item("a", "failed"), self.item("b", "failed")], "fixture")
        self.assertEqual(group["status"], "failed")

    def test_no_match_with_missing_is_missing_group(self):
        group = _or_group("catalyst", "catalyst", [self.item("a", "failed"), self.item("b", "missing")], "fixture")
        self.assertEqual(group["status"], "missing")


if __name__ == "__main__":
    unittest.main()
