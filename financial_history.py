from __future__ import annotations

from typing import Any

import pandas as pd

from v11_foundation import V11_CONFIG


YAHOO_FIELDS = {
    "revenue": ("income_statement", ["Total Revenue", "Operating Revenue"]),
    "gross_profit": ("income_statement", ["Gross Profit"]),
    "operating_profit": ("income_statement", ["Operating Income", "Operating Profit"]),
    "pretax_income": ("income_statement", ["Pretax Income", "Income Before Tax"]),
    "tax_provision": ("income_statement", ["Tax Provision", "Income Tax Expense"]),
    "net_income": ("income_statement", ["Net Income", "Net Income Common Stockholders"]),
    "eps": ("income_statement", ["Diluted EPS", "Basic EPS"]),
    "operating_cash_flow": (
        "cash_flow",
        ["Operating Cash Flow", "Total Cash From Operating Activities"],
    ),
    "current_assets": ("balance_sheet", ["Current Assets", "Total Current Assets"]),
    "inventory": ("balance_sheet", ["Inventory", "Inventories"]),
    "investment_securities": (
        "balance_sheet",
        [
            "Long Term Equity Investment",
            "Investments And Other Financial Assets",
            "Available For Sale Securities",
        ],
    ),
    "total_liabilities": (
        "balance_sheet",
        ["Total Liabilities Net Minority Interest", "Total Liabilities"],
    ),
    "total_equity": (
        "balance_sheet",
        ["Stockholders Equity", "Total Equity Gross Minority Interest", "Total Equity"],
    ),
    "cash_and_equivalents": (
        "balance_sheet",
        ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments", "Cash Financial"],
    ),
    "total_debt": ("balance_sheet", ["Total Debt"]),
}

METRIC_PRIORITY_GROUP = {
    "revenue": "long_term_financial_history",
    "gross_profit": "income_and_cash_flow_history",
    "operating_profit": "income_and_cash_flow_history",
    "pretax_income": "income_and_cash_flow_history",
    "tax_provision": "income_and_cash_flow_history",
    "net_income": "income_and_cash_flow_history",
    "eps": "long_term_financial_history",
    "operating_cash_flow": "income_and_cash_flow_history",
    "current_assets": "balance_sheet_details",
    "inventory": "balance_sheet_details",
    "investment_securities": "balance_sheet_details",
    "total_liabilities": "balance_sheet_details",
    "total_equity": "balance_sheet_details",
    "cash_and_equivalents": "balance_sheet_details",
    "total_debt": "balance_sheet_details",
}


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _period_text(value: Any) -> str:
    if hasattr(value, "date"):
        return str(value.date())
    return str(value)


def _extract_series(frame: pd.DataFrame, names: list[str]) -> tuple[str | None, list[dict]]:
    if frame is None or frame.empty:
        return None, []

    source_field = next((name for name in names if name in frame.index), None)
    if source_field is None:
        return None, []

    points = []
    for column in frame.columns:
        value = _safe_float(frame.loc[source_field, column])
        if value is not None:
            points.append({"period": _period_text(column), "value": value})

    points.sort(key=lambda point: point["period"])
    return source_field, points


def build_yahoo_series(
    financials: pd.DataFrame,
    annual_cash_flow: pd.DataFrame,
    annual_balance_sheet: pd.DataFrame,
    retrieved_at: str,
    currency: str | None,
) -> dict[str, dict]:
    frames = {
        "income_statement": financials,
        "cash_flow": annual_cash_flow,
        "balance_sheet": annual_balance_sheet,
    }
    result = {}

    for metric, (statement, names) in YAHOO_FIELDS.items():
        source_field, points = _extract_series(frames[statement], names)
        result[metric] = {
            "metric": metric,
            "status": "available" if points else "not_acquired",
            "source": "Yahoo Finance",
            "source_field": source_field,
            "statement": statement,
            "basis": "annual",
            "consolidation": "source_reported",
            "accounting_standard": "source_reported",
            "currency": currency,
            "retrieved_at": retrieved_at,
            "points": points,
            "point_count": len(points),
            "missing_reason": None if points else "field_not_returned_by_source",
        }
        if metric == "investment_securities":
            result[metric]["cash_value_eligible"] = False
            result[metric]["eligibility_reason"] = "Yahoo field scope may overlap current assets; verification is required."

    return result


def select_preferred_series(
    candidates: dict[str, dict],
    priority: list[str],
    minimum_points: int = 1,
) -> tuple[dict | None, list[dict]]:
    attempts = []
    for source in priority:
        candidate = candidates.get(source)
        if candidate is None:
            attempts.append({"source": source, "status": "not_acquired", "reason": "source_not_connected"})
            continue

        point_count = len(candidate.get("points") or [])
        if candidate.get("status") == "available" and point_count >= minimum_points:
            attempts.append({"source": source, "status": "selected", "reason": None})
            return candidate, attempts

        attempts.append({
            "source": source,
            "status": candidate.get("status", "not_acquired"),
            "reason": candidate.get("missing_reason") or f"requires_{minimum_points}_points",
        })

    return None, attempts


def _history_profile(revenue_series: dict | None) -> str:
    count = len((revenue_series or {}).get("points") or [])
    if count >= 6:
        return "standard_5y"
    if count >= 4:
        return "limited_3y"
    return "ipo_or_short_history"


def build_financial_history_bundle(
    financials: pd.DataFrame,
    annual_cash_flow: pd.DataFrame,
    annual_balance_sheet: pd.DataFrame,
    retrieved_at: str,
    currency: str | None = None,
    additional_sources: dict[str, dict[str, dict]] | None = None,
) -> dict:
    yahoo = build_yahoo_series(
        financials,
        annual_cash_flow,
        annual_balance_sheet,
        retrieved_at,
        currency,
    )
    additional_sources = additional_sources or {}
    priorities = V11_CONFIG["data_source_policy"]["priorities"]
    selected_series = {}
    source_attempts = {}

    for metric in YAHOO_FIELDS:
        candidates = {"Yahoo Finance": yahoo[metric]}
        for source, series_by_metric in additional_sources.items():
            if metric in series_by_metric:
                candidates[source] = series_by_metric[metric]

        group = METRIC_PRIORITY_GROUP[metric]
        selected, attempts = select_preferred_series(candidates, priorities[group])
        selected_series[metric] = selected or {
            "metric": metric,
            "status": "not_acquired",
            "source": None,
            "source_field": None,
            "points": [],
            "point_count": 0,
            "missing_reason": "no_source_returned_a_usable_series",
        }
        if metric == "investment_securities" and selected is None:
            selected_series[metric]["cash_value_eligible"] = False
            selected_series[metric]["eligibility_reason"] = "No source proved that the value is non-current and non-overlapping."
        source_attempts[metric] = attempts

    revenue = selected_series.get("revenue")
    return {
        "version": "1.1.0",
        "status": "available" if any(item.get("status") == "available" for item in selected_series.values()) else "not_acquired",
        "history_profile": _history_profile(revenue),
        "source_policy_version": V11_CONFIG["data_source_policy"]["version"],
        "period_mixing_allowed": False,
        "series": selected_series,
        "source_attempts": source_attempts,
        "capabilities": {
            "revenue_1y": len((revenue or {}).get("points") or []) >= 2,
            "revenue_3y_cagr": len((revenue or {}).get("points") or []) >= 4,
            "revenue_5y_cagr": len((revenue or {}).get("points") or []) >= 6,
        },
    }


def build_metric_lineage(
    metrics: dict,
    fiscal_period: str,
    retrieved_at: str,
    forecast_revision: dict,
) -> dict:
    yahoo_fields = {
        "revenue_growth_pct": "Total Revenue annual change",
        "operating_profit_growth_pct": "Operating Income annual change",
        "eps_growth_pct": "Diluted EPS or Basic EPS annual change",
        "operating_margin_pct": "Operating Income / Total Revenue",
        "latest_growth_pct": "Total Revenue annual change",
        "previous_growth_pct": "Total Revenue prior annual change",
        "margin_change_points": "Operating margin annual change",
        "free_cash_flow": "Free Cash Flow or Operating Cash Flow - Capital Expenditure",
        "net_debt": "Total Debt - Cash",
        "shares_outstanding": "Shares Outstanding",
        "market_price": "currentPrice, regularMarketPrice, fast_info, or latest close",
        "price_return_5d_pct": "Close history 5 trading days",
        "price_return_20d_pct": "Close history 20 trading days",
        "volume_ratio_20d": "latest Volume / prior 20-day average",
        "rs_63d_pct": "stock return - 1306.T return over 63 trading days",
        "rs_126d_pct": "stock return - 1306.T return over 126 trading days",
    }
    result = {}
    for metric, source_field in yahoo_fields.items():
        value = metrics.get(metric)
        result[metric] = {
            "status": "available" if value is not None else "not_acquired",
            "source": "Yahoo Finance",
            "source_field": source_field,
            "fiscal_period": fiscal_period if metric not in {"market_price", "price_return_5d_pct", "price_return_20d_pct", "volume_ratio_20d", "rs_63d_pct", "rs_126d_pct"} else None,
            "consolidation": "source_reported" if metric not in {"market_price", "price_return_5d_pct", "price_return_20d_pct", "volume_ratio_20d", "rs_63d_pct", "rs_126d_pct"} else None,
            "retrieved_at": retrieved_at,
            "missing_reason": None if value is not None else "source_value_missing",
        }

    revision_value = metrics.get("guidance_revision_pct")
    result["guidance_revision_pct"] = {
        "status": "available" if revision_value is not None else "not_acquired",
        "source": "J-Quants financial summary",
        "source_field": "FOP history",
        "fiscal_period": forecast_revision.get("fiscal_year_end"),
        "retrieved_at": retrieved_at,
        "missing_reason": None if revision_value is not None else forecast_revision.get("status", "source_value_missing"),
    }
    return result
