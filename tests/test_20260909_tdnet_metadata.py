import json
import unittest
from pathlib import Path

from tdnet_metadata import (
    assess_jquants_reflection,
    build_tdnet_metadata,
    classify_tdnet_title,
    deduplicate_and_apply_status,
)
from v11_foundation import V11_CONFIG


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "20260909_tdnet_disclosures.json"


def load_fixture() -> list[dict]:
    with FIXTURE_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


class TdnetMetadataModelTests(unittest.TestCase):
    def test_fixture_populates_required_metadata_and_normalizes_tickers(self):
        records = [build_tdnet_metadata(raw) for raw in load_fixture()]
        for record in records:
            self.assertTrue(record.disclosure_id)
            self.assertIsNotNone(record.disclosed_at)
            self.assertRegex(record.ticker, r"^[0-9A-Z]{4}$")
            self.assertTrue(record.company_name)
            self.assertTrue(record.title)
            self.assertTrue(record.category)
            self.assertTrue(record.importance)
            self.assertTrue(record.official_material_url.startswith("https://"))
            self.assertIsNotNone(record.retrieved_at)
            self.assertTrue(record.deduplication_key)
            self.assertIsNotNone(record.jquants_comparison.status)
        self.assertIn("472A", {record.ticker for record in records})
        self.assertIn("6857", {record.ticker for record in records})

    def test_configured_rules_cover_requested_categories(self):
        records = [build_tdnet_metadata(raw) for raw in load_fixture()]
        categories = {record.category for record in records}
        self.assertTrue({
            "earnings_forecast_revision",
            "dividend_forecast_revision",
            "financial_results",
            "share_buyback",
            "stock_split",
            "merger_acquisition",
            "new_business",
            "major_order",
            "medium_term_plan",
            "equity_financing",
        }.issubset(categories))

    def test_classification_rules_are_replaceable_from_configuration(self):
        custom_rules = {
            "classification_rules": [{
                "category": "major_order",
                "label": "特別受注",
                "importance": "critical",
                "match_any": ["特別案件受注"],
            }],
            "status_rules": V11_CONFIG["tdnet"]["status_rules"],
        }
        result = classify_tdnet_title("特別案件受注のお知らせ", custom_rules)
        self.assertEqual(result["category"], "major_order")
        self.assertEqual(result["importance"], "critical")


class TdnetLifecycleTests(unittest.TestCase):
    def test_deduplicates_and_preserves_correction_and_deletion_history(self):
        records = [build_tdnet_metadata(raw) for raw in load_fixture()[:4]]
        retained, duplicates = deduplicate_and_apply_status(records)
        by_id = {record.disclosure_id: record for record in retained}
        self.assertEqual(len(retained), 3)
        self.assertEqual(duplicates, ["TD-6857-001"])
        self.assertEqual(by_id["TD-6857-001"].record_status, "corrected")
        self.assertEqual(by_id["TD-6857-002"].record_status, "deleted")
        self.assertEqual(by_id["TD-6857-003"].record_status, "deleted")


class JQuantsReflectionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = load_fixture()

    def test_eligible_financial_disclosure_is_reflected_by_matching_payload(self):
        disclosure = build_tdnet_metadata(self.fixture[0])
        compared = assess_jquants_reflection(disclosure, {
            "record_id": "JQ-6857-001",
            "ticker": "6857",
            "comparison_kind": "earnings_forecast",
            "fiscal_period": "2026-03-31",
            "comparison_payload": {"forecast_operating_profit": 454000000000},
            "updated_at": "2026-01-29T00:00:00Z",
        })
        self.assertTrue(compared.jquants_comparison.eligible)
        self.assertEqual(compared.jquants_comparison.status, "reflected")

    def test_date_is_not_enough_when_comparison_payload_differs(self):
        disclosure = build_tdnet_metadata(self.fixture[0])
        compared = assess_jquants_reflection(disclosure, {
            "record_id": "JQ-6857-002",
            "ticker": "6857",
            "comparison_kind": "earnings_forecast",
            "fiscal_period": "2026-03-31",
            "comparison_payload": {"forecast_operating_profit": 400000000000},
            "updated_at": "2026-09-09T00:00:00Z",
        })
        self.assertEqual(compared.jquants_comparison.status, "not_reflected")
        self.assertIn("payload", compared.jquants_comparison.reason)

    def test_missing_matching_jquants_record_is_not_reflected(self):
        disclosure = build_tdnet_metadata(self.fixture[5])
        compared = assess_jquants_reflection(disclosure, None)
        self.assertEqual(compared.category, "financial_results")
        self.assertEqual(compared.jquants_comparison.status, "not_reflected")

    def test_nonfinancial_important_disclosure_is_not_applicable(self):
        disclosure = build_tdnet_metadata(self.fixture[4])
        compared = assess_jquants_reflection(disclosure, {
            "record_id": "JQ-472A-001",
            "ticker": "472A",
            "comparison_kind": "financial_results",
            "fiscal_period": "2026-12-31",
            "comparison_payload": {"event": "subsidiary"},
            "updated_at": "2026-09-09T00:00:00Z",
        })
        self.assertEqual(compared.category, "merger_acquisition")
        self.assertEqual(compared.importance, "high")
        self.assertFalse(compared.jquants_comparison.eligible)
        self.assertEqual(compared.jquants_comparison.status, "not_applicable")

    def test_deleted_record_is_excluded_from_reflection_checks(self):
        disclosure = build_tdnet_metadata(self.fixture[3])
        self.assertEqual(disclosure.record_status, "deleted")
        self.assertFalse(disclosure.jquants_comparison.eligible)
        self.assertEqual(disclosure.jquants_comparison.status, "not_applicable")


class TdnetFeatureFlagTests(unittest.TestCase):
    def test_metadata_is_enabled_but_production_auto_fetch_is_off(self):
        self.assertTrue(V11_CONFIG["feature_flags"]["tdnet_metadata"])
        self.assertFalse(V11_CONFIG["feature_flags"]["tdnet_auto_fetch"])
        self.assertTrue(V11_CONFIG["tdnet"]["auto_fetch_requires_jpx_approval"])


if __name__ == "__main__":
    unittest.main()
