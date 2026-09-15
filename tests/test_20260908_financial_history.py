import unittest

import pandas as pd

from financial_history import build_financial_history_bundle, select_preferred_series


def series(source: str, count: int) -> dict:
    return {
        "metric": "revenue",
        "status": "available",
        "source": source,
        "source_field": "Revenue",
        "points": [
            {"period": f"{2020 + index}-03-31", "value": float(100 + index)}
            for index in range(count)
        ],
        "point_count": count,
        "missing_reason": None,
    }


class SourceSelectionTests(unittest.TestCase):
    def test_edinet_wins_without_mixing_series(self):
        edinet = series("EDINET", 6)
        yahoo = series("Yahoo Finance", 6)
        selected, attempts = select_preferred_series(
            {"EDINET": edinet, "Yahoo Finance": yahoo},
            ["EDINET", "J-Quants financial details", "Yahoo Finance"],
            minimum_points=6,
        )
        self.assertIs(selected, edinet)
        self.assertEqual(selected["source"], "EDINET")
        self.assertEqual(len(selected["points"]), 6)
        self.assertEqual(attempts, [{"source": "EDINET", "status": "selected", "reason": None}])

    def test_insufficient_higher_priority_source_falls_through_as_whole_series(self):
        edinet = series("EDINET", 3)
        yahoo = series("Yahoo Finance", 6)
        selected, attempts = select_preferred_series(
            {"EDINET": edinet, "Yahoo Finance": yahoo},
            ["EDINET", "J-Quants financial details", "Yahoo Finance"],
            minimum_points=6,
        )
        self.assertIs(selected, yahoo)
        self.assertEqual(selected["source"], "Yahoo Finance")
        self.assertEqual(len(selected["points"]), 6)
        self.assertEqual(attempts[0]["source"], "EDINET")
        self.assertEqual(attempts[1]["source"], "J-Quants financial details")


class YahooHistoryTests(unittest.TestCase):
    def test_builds_available_history_and_never_fills_missing_with_zero(self):
        columns = pd.to_datetime(["2025-03-31", "2024-03-31", "2023-03-31", "2022-03-31"])
        financials = pd.DataFrame(
            [[140.0, 130.0, 120.0, 100.0], [30.0, 25.0, 20.0, 15.0]],
            index=["Total Revenue", "Operating Income"],
            columns=columns,
        )
        cash_flow = pd.DataFrame(
            [[20.0, 18.0, 16.0, 15.0]],
            index=["Operating Cash Flow"],
            columns=columns,
        )
        balance_sheet = pd.DataFrame(
            [[200.0, 190.0, 180.0, 170.0]],
            index=["Current Assets"],
            columns=columns,
        )

        bundle = build_financial_history_bundle(
            financials,
            cash_flow,
            balance_sheet,
            "2026-09-08T00:00:00Z",
            "JPY",
        )

        self.assertEqual(bundle["history_profile"], "limited_3y")
        self.assertTrue(bundle["capabilities"]["revenue_3y_cagr"])
        self.assertFalse(bundle["capabilities"]["revenue_5y_cagr"])
        self.assertEqual(bundle["series"]["revenue"]["source"], "Yahoo Finance")
        self.assertEqual(bundle["series"]["inventory"]["status"], "not_acquired")
        self.assertEqual(bundle["series"]["inventory"]["points"], [])
        self.assertFalse(bundle["series"]["investment_securities"]["cash_value_eligible"])

    def test_ipo_history_is_labeled_separately(self):
        columns = pd.to_datetime(["2025-12-31", "2024-12-31"])
        financials = pd.DataFrame([[120.0, 100.0]], index=["Total Revenue"], columns=columns)
        bundle = build_financial_history_bundle(
            financials,
            pd.DataFrame(),
            pd.DataFrame(),
            "2026-09-08T00:00:00Z",
            "JPY",
        )
        self.assertEqual(bundle["history_profile"], "ipo_or_short_history")
        self.assertTrue(bundle["capabilities"]["revenue_1y"])
        self.assertFalse(bundle["capabilities"]["revenue_3y_cagr"])


if __name__ == "__main__":
    unittest.main()
