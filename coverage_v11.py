from __future__ import annotations

from collections import Counter
from typing import Any

from v11_foundation import V11_CONFIG


COUNTED_STATUSES = {"available", "not_acquired", "not_calculable", "short_history"}


def _check(status: str, reason: str | None = None) -> dict:
    return {"status": status, "reason": reason}


def _item(
    key: str,
    status: str,
    *,
    value: Any = None,
    source: str | list[str] | None = None,
    fiscal_period: str | None = None,
    consolidation: str | None = None,
    missing_reason: str | None = None,
    required_history_points: int | None = None,
    actual_history_points: int | None = None,
    checks: dict[str, dict] | None = None,
) -> dict:
    return {
        "key": key,
        "status": status,
        "value": value,
        "source": source,
        "fiscal_period": fiscal_period,
        "consolidation": consolidation,
        "missing_reason": missing_reason,
        "required_history_points": required_history_points,
        "actual_history_points": actual_history_points,
        "checks": checks or {},
    }


def _level(percentage: float | None) -> str:
    if percentage is None:
        return "not_applicable"
    thresholds = V11_CONFIG["coverage"]["confidence_thresholds"]
    if percentage >= float(thresholds["high_min_pct"]):
        return "high"
    if percentage >= float(thresholds["medium_min_pct"]):
        return "medium"
    return "low"


def _module(items: list[dict]) -> dict:
    eligible = [item for item in items if item["status"] != "not_applicable"]
    available = [item for item in eligible if item["status"] == "available"]
    denominator = len(eligible)
    numerator = len(available)
    percentage = round(numerator / denominator * 100, 1) if denominator else None
    reasons = [
        {"key": item["key"], "status": item["status"], "reason": item.get("missing_reason")}
        for item in eligible
        if item["status"] != "available"
    ]
    return {
        "percentage": percentage,
        "level": _level(percentage),
        "numerator": numerator,
        "denominator": denominator,
        "excluded_not_applicable": len(items) - denominator,
        "status_counts": dict(Counter(item["status"] for item in items)),
        "items": items,
        "unavailable_reasons": reasons,
    }


def _effective_status(
    base_status: str,
    *,
    source: Any,
    fiscal_period: str | None,
    expected_fiscal_period: str | None,
    consolidation: str | None,
    expected_consolidation: str | None,
    required_history_points: int | None = None,
    actual_history_points: int | None = None,
) -> tuple[str, str | None, dict[str, dict]]:
    checks = {
        "source": _check("passed" if source else "failed", None if source else "data_source_missing"),
        "fiscal_period": _check(
            "not_applicable" if expected_fiscal_period is None else "passed" if fiscal_period == expected_fiscal_period else "failed",
            None if expected_fiscal_period is None or fiscal_period == expected_fiscal_period else "fiscal_period_mismatch",
        ),
        "consolidation": _check(
            "not_applicable" if expected_consolidation is None else "passed" if consolidation == expected_consolidation else "failed",
            None if expected_consolidation is None or consolidation == expected_consolidation else "consolidation_mismatch",
        ),
        "history_length": _check(
            "not_applicable" if required_history_points is None else "passed" if (actual_history_points or 0) >= required_history_points else "failed",
            None if required_history_points is None or (actual_history_points or 0) >= required_history_points else f"requires_{required_history_points}_points_but_found_{actual_history_points or 0}",
        ),
        "calculability": _check("passed" if base_status == "available" else "failed", None if base_status == "available" else base_status),
    }
    if base_status == "not_applicable":
        return "not_applicable", "not_applicable", checks
    if base_status in COUNTED_STATUSES and base_status != "available":
        return base_status, None, checks
    if not source:
        return "not_acquired", "data_source_missing", checks
    if required_history_points is not None and (actual_history_points or 0) < required_history_points:
        return "short_history", f"requires_{required_history_points}_points_but_found_{actual_history_points or 0}", checks
    if expected_fiscal_period is not None and fiscal_period != expected_fiscal_period:
        return "not_calculable", "fiscal_period_mismatch", checks
    if expected_consolidation is not None and consolidation != expected_consolidation:
        return "not_calculable", "consolidation_mismatch", checks
    return "available", None, checks


def _history_item(history: dict, key: str, required_points: int, expected_period: str | None, expected_consolidation: str | None) -> dict:
    series = (history.get("series") or {}).get(key) or {}
    points = series.get("points") or []
    latest_period = points[-1].get("period") if points else None
    status, reason, checks = _effective_status(
        series.get("status", "not_acquired"),
        source=series.get("source"),
        fiscal_period=latest_period,
        expected_fiscal_period=expected_period,
        consolidation=series.get("consolidation"),
        expected_consolidation=expected_consolidation,
        required_history_points=required_points,
        actual_history_points=len(points),
    )
    return _item(
        key,
        status,
        value=points[-1].get("value") if points else None,
        source=series.get("source"),
        fiscal_period=latest_period,
        consolidation=series.get("consolidation"),
        missing_reason=series.get("missing_reason") or reason,
        required_history_points=required_points,
        actual_history_points=len(points),
        checks=checks,
    )


def _response_item(
    key: str,
    metric: dict | None,
    expected_period: str | None = None,
    expected_consolidation: str | None = None,
    required_history_points: int | None = None,
    actual_history_points: int | None = None,
) -> dict:
    metric = metric or {}
    periods = metric.get("periods") or []
    actual_points = len(periods) if actual_history_points is None and required_history_points is not None else actual_history_points
    status, reason, checks = _effective_status(
        metric.get("status", "not_acquired"),
        source=metric.get("source"),
        fiscal_period=metric.get("fiscal_period"),
        expected_fiscal_period=expected_period,
        consolidation=metric.get("consolidation"),
        expected_consolidation=expected_consolidation,
        required_history_points=required_history_points,
        actual_history_points=actual_points,
    )
    return _item(
        key,
        status,
        value=metric.get("value"),
        source=metric.get("source"),
        fiscal_period=metric.get("fiscal_period"),
        consolidation=metric.get("consolidation"),
        missing_reason=metric.get("missing_reason") or reason,
        required_history_points=required_history_points,
        actual_history_points=actual_points,
        checks=checks,
    )


def _simple_item(key: str, value: Any, lineage: dict | None, expected_period: str | None, fiscal_required: bool = True, expected_consolidation: str | None = None) -> dict:
    lineage = lineage or {}
    base_status = "available" if value is not None and lineage.get("status") == "available" else lineage.get("status", "not_acquired")
    status, reason, checks = _effective_status(
        base_status,
        source=lineage.get("source"),
        fiscal_period=lineage.get("fiscal_period"),
        expected_fiscal_period=expected_period if fiscal_required else None,
        consolidation=lineage.get("consolidation"),
        expected_consolidation=expected_consolidation,
    )
    return _item(
        key,
        status,
        value=value,
        source=lineage.get("source"),
        fiscal_period=lineage.get("fiscal_period"),
        consolidation=lineage.get("consolidation"),
        missing_reason=lineage.get("missing_reason") or reason,
        checks=checks,
    )


def _sanity_flags(growth_quality: dict) -> list[dict]:
    roic = growth_quality.get("roic") or {}
    value = roic.get("value")
    if roic.get("status") != "available" or not isinstance(value, (int, float)):
        return []
    thresholds = V11_CONFIG["coverage"]["roic_sanity_thresholds"]
    if value >= float(thresholds["extreme_review_pct"]):
        code = "extreme_roic_review"
        threshold = thresholds["extreme_review_pct"]
    elif value >= float(thresholds["high_review_pct"]):
        code = "high_roic_review"
        threshold = thresholds["high_review_pct"]
    else:
        return []
    return [{
        "code": code,
        "metric": "roic",
        "value_pct": value,
        "threshold_pct": threshold,
        "message": "ROICの分母や一時的な特殊要因を人間が確認してください。",
        "affects_score": False,
        "affects_coverage": False,
    }]


def build_data_coverage(
    ticker: str,
    *,
    fiscal_period: str | None,
    financial_history: dict,
    metrics: dict,
    metric_lineage: dict,
    cash_value: dict,
    growth_quality: dict,
    market_momentum: dict,
    retrieved_at: str,
) -> dict:
    available_consolidations = [
        series.get("consolidation")
        for series in (financial_history.get("series") or {}).values()
        if series.get("status") == "available" and series.get("consolidation")
    ]
    expected_consolidation = Counter(available_consolidations).most_common(1)[0][0] if available_consolidations else None

    core_keys = (
        "revenue_growth_pct", "operating_profit_growth_pct", "eps_growth_pct", "operating_margin_pct",
        "free_cash_flow", "net_debt", "shares_outstanding",
    )
    core_items = [_simple_item(key, metrics.get(key), metric_lineage.get(key), fiscal_period, expected_consolidation=expected_consolidation) for key in core_keys]

    raw = cash_value.get("raw_inputs") or {}
    cash_items = [
        _response_item(key, raw.get(key), fiscal_period if key in {"current_assets", "inventory", "investment_securities", "total_liabilities", "roic"} else None, expected_consolidation if key in {"current_assets", "inventory", "investment_securities", "total_liabilities"} else None)
        for key in ("current_assets", "inventory", "investment_securities", "total_liabilities", "market_cap", "per", "roic")
    ]
    cash_items.extend([
        _response_item("net_cash", cash_value.get("net_cash"), fiscal_period, expected_consolidation),
        _response_item("ncr", (cash_value.get("ncr") or {}).get("display"), fiscal_period, expected_consolidation),
        _response_item("per_cn", cash_value.get("per_cn"), fiscal_period, expected_consolidation),
        _response_item("dpup", cash_value.get("dpup"), fiscal_period, expected_consolidation),
    ])

    revenue_growth = growth_quality.get("revenue_growth") or {}
    profitability = growth_quality.get("profitability") or {}
    growth_items = [
        _response_item("revenue_cagr_5y", revenue_growth.get("five_year_cagr"), fiscal_period, expected_consolidation, 6),
        _response_item("revenue_cagr_3y", revenue_growth.get("three_year_cagr"), fiscal_period, expected_consolidation, 4),
        _response_item("revenue_growth_1y", revenue_growth.get("one_year"), fiscal_period, expected_consolidation, 2),
        _response_item("eps_cagr_5y", (growth_quality.get("eps_growth") or {}).get("five_year_cagr"), fiscal_period, expected_consolidation, 6),
        _response_item("gross_margin", profitability.get("gross_margin"), fiscal_period, expected_consolidation, 2),
        _response_item("operating_margin", profitability.get("operating_margin"), fiscal_period, expected_consolidation, 2),
        _response_item("operating_cash_flow", growth_quality.get("operating_cash_flow"), fiscal_period, expected_consolidation, 1),
        _response_item("roic", growth_quality.get("roic"), fiscal_period, expected_consolidation, 1),
        _response_item(
            "growth_trend",
            growth_quality.get("growth_trend"),
            fiscal_period,
            expected_consolidation,
            required_history_points=6,
            actual_history_points=len((revenue_growth.get("five_year_cagr") or {}).get("periods") or []),
        ),
    ]

    market_values = {
        "market_price": metrics.get("market_price"),
        "price_return_5d_pct": market_momentum.get("price_return_5d_pct"),
        "price_return_20d_pct": market_momentum.get("price_return_20d_pct"),
        "volume_ratio_20d": market_momentum.get("volume_ratio_20d"),
        "rs_63d_pct": market_momentum.get("rs_63d_pct"),
        "rs_126d_pct": market_momentum.get("rs_126d_pct"),
    }
    market_items = [
        _simple_item(key, value, metric_lineage.get(key), None, fiscal_required=False)
        for key, value in market_values.items()
    ]

    tdnet_reason = "production_auto_fetch_disabled_pending_jpx_approval"
    tdnet_items = [
        _item(key, "not_applicable", missing_reason=tdnet_reason, checks={"production_fetch": _check("not_applicable", tdnet_reason)})
        for key in ("latest_disclosure", "classification", "correction_deletion_status", "jquants_reflection")
    ]

    modules = {
        "core_financial_coverage": _module(core_items),
        "cash_value_coverage": _module(cash_items),
        "growth_quality_coverage": _module(growth_items),
        "market_coverage": _module(market_items),
        "tdnet_coverage": _module(tdnet_items),
    }
    overall_items = [item for module in modules.values() for item in module["items"]]
    overall = _module(overall_items)
    modules["overall_coverage"] = overall

    lookup = {
        **{f"core_financial.{item['key']}": item for item in core_items},
        **{f"cash_value.{item['key']}": item for item in cash_items},
        **{f"growth_quality.{item['key']}": item for item in growth_items},
        **{f"market.{item['key']}": item for item in market_items},
    }
    readiness = {}
    for classification, required_keys in V11_CONFIG["coverage"]["classification_required_items"].items():
        missing = [key for key in required_keys if (lookup.get(key) or {}).get("status") != "available"]
        readiness[classification] = {
            "status": "ready" if not missing else "insufficient_data",
            "required_items": required_keys,
            "missing_required_items": missing,
            "coverage_percentage_not_sufficient_alone": True,
        }

    return {
        "version": "1.1.0",
        "ticker": ticker,
        "status": "available",
        "retrieved_at": retrieved_at,
        "thresholds": V11_CONFIG["coverage"]["confidence_thresholds"],
        "not_applicable_is_excluded_from_denominator": True,
        **modules,
        "classification_readiness": readiness,
        "sanity_flags": _sanity_flags(growth_quality),
        "independent_from_existing_data_coverage_pct": True,
        "independent_from_existing_scores": True,
    }
