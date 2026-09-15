from __future__ import annotations

from typing import Any

from v11_foundation import V11_CONFIG


FORMULAS = {
    "net_cash": "current_assets - inventory * 0.3 + investment_securities * 0.7 - total_liabilities",
    "ncr": "net_cash / market_cap * 100",
    "per_cn": "per * (100 - ncr_calculation_pct) / 100",
    "dpup": "max(roic_display_pct, 0) / per_cn",
}


def metric(
    value: float | bool | None = None,
    status: str = "available",
    *,
    source: str | None = None,
    fiscal_period: str | None = None,
    consolidation: str | None = None,
    currency: str | None = None,
    retrieved_at: str | None = None,
    missing_reason: str | None = None,
    **metadata: Any,
) -> dict:
    return {
        "value": value,
        "status": status,
        "source": source,
        "fiscal_period": fiscal_period,
        "consolidation": consolidation,
        "currency": currency,
        "retrieved_at": retrieved_at,
        "missing_reason": missing_reason,
        **metadata,
    }


def _unavailable(reason: str, status: str = "not_calculable", **metadata: Any) -> dict:
    return metric(None, status, missing_reason=reason, **metadata)


def _number(item: dict | None) -> float | None:
    if not item or item.get("status") != "available":
        return None
    value = item.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _series_metric(history: dict, name: str, fiscal_period: str) -> dict:
    item = (history.get("series") or {}).get(name) or {}
    if item.get("status") != "available":
        return _unavailable(
            item.get("missing_reason") or "source_value_missing",
            "not_acquired",
            source=item.get("source"),
            fiscal_period=fiscal_period,
            consolidation=item.get("consolidation"),
            currency=item.get("currency"),
            retrieved_at=item.get("retrieved_at"),
        )
    point = next((point for point in item.get("points") or [] if point.get("period") == fiscal_period), None)
    if point is None:
        return _unavailable(
            "fiscal_period_mismatch_or_missing",
            source=item.get("source"),
            fiscal_period=fiscal_period,
            consolidation=item.get("consolidation"),
            currency=item.get("currency"),
            retrieved_at=item.get("retrieved_at"),
        )
    return metric(
        point.get("value"),
        source=item.get("source"),
        fiscal_period=fiscal_period,
        consolidation=item.get("consolidation"),
        currency=item.get("currency"),
        retrieved_at=item.get("retrieved_at"),
        source_field=item.get("source_field"),
        basis=item.get("basis"),
        cash_value_eligible=item.get("cash_value_eligible"),
        eligibility_reason=item.get("eligibility_reason"),
        balance_sheet_scope=item.get("balance_sheet_scope"),
        non_overlapping_with_current_assets=item.get("non_overlapping_with_current_assets"),
    )


def _five_year_sales_cagr(history: dict, fiscal_period: str) -> dict:
    item = (history.get("series") or {}).get("revenue") or {}
    points = [point for point in item.get("points") or [] if point.get("period") <= fiscal_period]
    if item.get("status") != "available" or len(points) < 6 or points[-1].get("period") != fiscal_period:
        return _unavailable(
            "five_year_revenue_history_unavailable_or_period_mismatch",
            "not_acquired",
            source=item.get("source"),
            fiscal_period=fiscal_period,
            consolidation=item.get("consolidation"),
            currency=item.get("currency"),
            retrieved_at=item.get("retrieved_at"),
        )
    selected = points[-6:]
    first = selected[0].get("value")
    last = selected[-1].get("value")
    if not isinstance(first, (int, float)) or not isinstance(last, (int, float)) or first <= 0 or last < 0:
        return _unavailable(
            "five_year_revenue_cagr_requires_positive_start_and_nonnegative_end",
            source=item.get("source"),
            fiscal_period=fiscal_period,
            consolidation=item.get("consolidation"),
            currency=item.get("currency"),
            retrieved_at=item.get("retrieved_at"),
        )
    value = ((float(last) / float(first)) ** (1 / 5) - 1) * 100
    return metric(
        value,
        source=item.get("source"),
        fiscal_period=fiscal_period,
        consolidation=item.get("consolidation"),
        currency=item.get("currency"),
        retrieved_at=item.get("retrieved_at"),
    )


def build_cash_value_inputs(
    history: dict,
    *,
    fiscal_period: str,
    market_cap: float | None,
    per: float | None,
    retrieved_at: str,
    market_cap_source: str,
    per_source: str,
) -> dict[str, dict]:
    inputs = {
        name: _series_metric(history, name, fiscal_period)
        for name in ("current_assets", "inventory", "investment_securities", "total_liabilities")
    }
    balance_currency = inputs["current_assets"].get("currency")
    inputs["market_cap"] = (
        metric(market_cap, source=market_cap_source, currency=balance_currency, retrieved_at=retrieved_at)
        if market_cap is not None and market_cap > 0
        else _unavailable("market_cap_missing_or_not_positive", "not_acquired", source=market_cap_source, retrieved_at=retrieved_at)
    )
    inputs["per"] = (
        metric(per, source=per_source, retrieved_at=retrieved_at)
        if per is not None and per > 0
        else _unavailable(
            "per_missing" if per is None else "per_not_positive_loss_making_company",
            "not_acquired" if per is None else "not_calculable",
            source=per_source,
            retrieved_at=retrieved_at,
        )
    )
    inputs["roic"] = _unavailable(
        "verified_roic_source_and_fiscal_period_unavailable",
        "not_acquired",
        fiscal_period=fiscal_period,
        retrieved_at=retrieved_at,
    )
    inputs["sales_cagr_5y"] = _five_year_sales_cagr(history, fiscal_period)
    inputs["operating_cash_flow"] = _series_metric(history, "operating_cash_flow", fiscal_period)
    return inputs


def _balance_sheet_validation(inputs: dict[str, dict]) -> list[str]:
    names = ("current_assets", "inventory", "investment_securities", "total_liabilities")
    reasons = []
    for name in names:
        item = inputs.get(name) or {}
        if _number(item) is None:
            reasons.append(f"{name}:{item.get('missing_reason') or 'value_missing'}")

    investment = inputs.get("investment_securities") or {}
    if investment.get("cash_value_eligible") is not True:
        reasons.append("investment_securities:eligibility_not_verified")
    if investment.get("balance_sheet_scope") != "non_current_assets":
        reasons.append("investment_securities:non_current_scope_not_verified")
    if investment.get("non_overlapping_with_current_assets") is not True:
        reasons.append("investment_securities:possible_overlap_with_current_assets")

    periods = {inputs[name].get("fiscal_period") for name in names if inputs.get(name)}
    if None in periods or len(periods) != 1:
        reasons.append("balance_sheet:fiscal_period_mismatch")
    consolidations = {inputs[name].get("consolidation") for name in names if inputs.get(name)}
    if len(consolidations) != 1 or next(iter(consolidations), None) not in {"consolidated", "non_consolidated"}:
        reasons.append("balance_sheet:consolidation_not_verified_or_mismatch")
    currencies = {inputs[name].get("currency") for name in names if inputs.get(name)}
    if None in currencies or len(currencies) != 1:
        reasons.append("balance_sheet:currency_not_verified_or_mismatch")
    return list(dict.fromkeys(reasons))


def _linear_score(value: float, minimum: float, full: float) -> float:
    if full <= minimum:
        raise ValueError("full score threshold must be greater than minimum")
    return round(max(0.0, min(100.0, (value - minimum) / (full - minimum) * 100)), 2)


def _band_score(value: float, rules: dict, direction: str) -> float:
    for band in rules["bands"]:
        threshold = float(band[direction])
        if (direction == "lte" and value <= threshold) or (direction == "gte" and value >= threshold):
            return float(band["score"])
    return float(rules["default_score"])


def calculate_cash_value(
    ticker: str,
    inputs: dict[str, dict],
    *,
    sector: str | None,
    retrieved_at: str,
    profile: str | None = None,
) -> dict:
    config = V11_CONFIG["cash_value_score"]
    calculation = config["calculation"]
    profile_name = profile or config["active_profile"]
    weights = config["profiles"][profile_name]
    balance_reasons = _balance_sheet_validation(inputs)
    common = {
        "source": sorted({item.get("source") for item in inputs.values() if item.get("source")}),
        "fiscal_period": (inputs.get("current_assets") or {}).get("fiscal_period"),
        "consolidation": (inputs.get("current_assets") or {}).get("consolidation"),
        "retrieved_at": retrieved_at,
    }

    if balance_reasons:
        net_cash = _unavailable("; ".join(balance_reasons), **common)
    else:
        net_cash_value = (
            _number(inputs["current_assets"])
            - _number(inputs["inventory"]) * calculation["inventory_discount_factor"]
            + _number(inputs["investment_securities"]) * calculation["investment_securities_factor"]
            - _number(inputs["total_liabilities"])
        )
        net_cash = metric(net_cash_value, **common)

    market_cap = _number(inputs.get("market_cap"))
    if _number(net_cash) is None or market_cap is None or market_cap <= 0:
        reason = net_cash.get("missing_reason") or (inputs.get("market_cap") or {}).get("missing_reason") or "market_cap_missing_or_not_positive"
        ncr_display = _unavailable(reason, **common)
        ncr_calculation = _unavailable(reason, **common)
        cap_applied = False
    else:
        ncr_value = _number(net_cash) / market_cap * 100
        ncr_calculation_value = min(ncr_value, float(calculation["ncr_calculation_cap_pct"]))
        cap_applied = ncr_value > float(calculation["ncr_calculation_cap_pct"])
        ncr_display = metric(ncr_value, **common)
        ncr_calculation = metric(ncr_calculation_value, **common)

    per_value = _number(inputs.get("per"))
    ncr_calculation_value = _number(ncr_calculation)
    if per_value is None or per_value <= 0 or ncr_calculation_value is None:
        reason = (inputs.get("per") or {}).get("missing_reason") or ncr_calculation.get("missing_reason") or "per_cn_inputs_missing"
        per_cn = _unavailable(reason, **common)
    else:
        per_cn_value = per_value * (100 - ncr_calculation_value) / 100
        per_cn = (
            metric(per_cn_value, **common)
            if per_cn_value > 0
            else _unavailable("per_cn_not_positive", **common)
        )

    roic_display = dict(inputs.get("roic") or _unavailable("roic_missing", "not_acquired"))
    roic_value = _number(roic_display)
    roic_dpup_input = (
        metric(max(roic_value, 0.0), **{key: roic_display.get(key) for key in ("source", "fiscal_period", "consolidation", "currency", "retrieved_at")})
        if roic_value is not None
        else _unavailable(roic_display.get("missing_reason") or "roic_missing", roic_display.get("status", "not_acquired"))
    )

    normalized_sector = (sector or "").casefold()
    excluded_group = next((
        group
        for group, keywords in config["dpup_not_applicable_sectors"].items()
        if any(keyword.casefold() in normalized_sector for keyword in keywords)
    ), None)
    per_cn_value = _number(per_cn)
    roic_dpup_value = _number(roic_dpup_input)
    if excluded_group:
        dpup = _unavailable(f"dpup_not_applicable:{excluded_group}", "not_applicable", **common)
    elif per_cn_value is None or roic_dpup_value is None:
        dpup = _unavailable(per_cn.get("missing_reason") or roic_dpup_input.get("missing_reason") or "dpup_inputs_missing", **common)
    else:
        dpup = metric(roic_dpup_value / per_cn_value, **common)

    scoring = config["scoring"]
    materials: dict[str, dict] = {}

    def material(name: str, raw_value: float | bool | None, normalized_score: float | None, status: str, reason: str | None = None) -> None:
        materials[name] = {
            "raw_value": raw_value,
            "normalized_score": normalized_score,
            "weight": weights[name],
            "status": status,
            "missing_reason": reason,
        }

    if ncr_calculation_value is None:
        material("ncr", None, None, ncr_calculation["status"], ncr_calculation.get("missing_reason"))
    else:
        rules = scoring["ncr"]
        material("ncr", ncr_calculation_value, _linear_score(ncr_calculation_value, rules["minimum_pct"], rules["full_score_pct"]), "available")

    if per_cn_value is None:
        material("per_cn", None, None, per_cn["status"], per_cn.get("missing_reason"))
    else:
        material("per_cn", per_cn_value, _band_score(per_cn_value, scoring["per_cn"], "lte"), "available")

    dpup_value = _number(dpup)
    if dpup.get("status") == "not_applicable":
        material("dpup", None, None, "not_applicable", dpup.get("missing_reason"))
    elif dpup_value is None:
        material("dpup", None, None, dpup["status"], dpup.get("missing_reason"))
    else:
        material("dpup", dpup_value, _band_score(dpup_value, scoring["dpup"], "gte"), "available")

    sales_cagr = _number(inputs.get("sales_cagr_5y"))
    if sales_cagr is None or per_value is None:
        reason = (inputs.get("sales_cagr_5y") or {}).get("missing_reason") or (inputs.get("per") or {}).get("missing_reason") or "sales_cagr_comparison_inputs_missing"
        material("sales_cagr_5y_gt_per", None, None, "not_calculable", reason)
    else:
        matched = sales_cagr > per_value
        material("sales_cagr_5y_gt_per", matched, scoring["sales_cagr_5y_gt_per"]["matched_score" if matched else "failed_score"], "available")

    if roic_value is None:
        material("roic", None, None, roic_display.get("status", "not_acquired"), roic_display.get("missing_reason"))
    else:
        rules = scoring["roic"]
        material("roic", roic_value, _linear_score(roic_value, rules["minimum_pct"], rules["full_score_pct"]), "available")

    operating_cash_flow = _number(inputs.get("operating_cash_flow"))
    if operating_cash_flow is None:
        item = inputs.get("operating_cash_flow") or {}
        material("operating_cash_flow", None, None, item.get("status", "not_acquired"), item.get("missing_reason"))
    else:
        score = scoring["operating_cash_flow"]["positive_score" if operating_cash_flow > 0 else "non_positive_score"]
        material("operating_cash_flow", operating_cash_flow, score, "available")

    required_materials = ["ncr", "per_cn"] + ([] if materials["dpup"]["status"] == "not_applicable" else ["dpup"])
    blocking = [name for name in required_materials if materials[name]["status"] != "available"]
    active = [item for item in materials.values() if item["status"] == "available"]
    primary_statuses = [materials[name]["status"] for name in ("ncr", "per_cn", "dpup")]
    if all(status == "not_applicable" for status in primary_statuses):
        score = metric(None, "not_applicable", missing_reason="cash_value_primary_materials_not_applicable", profile=profile_name, weights=weights, **common)
    elif blocking:
        score = metric(None, "insufficient_data", missing_reason="missing_primary_score_materials:" + ",".join(blocking), profile=profile_name, weights=weights, **common)
    elif not active:
        score = metric(None, "not_applicable", missing_reason="no_applicable_score_materials", profile=profile_name, weights=weights, **common)
    else:
        active_weight = sum(float(item["weight"]) for item in active)
        score_value = sum(float(item["normalized_score"]) * float(item["weight"]) for item in active) / active_weight
        score = metric(
            round(score_value, 1),
            profile=profile_name,
            weights=weights,
            active_weight=active_weight,
            excluded_materials=[name for name, item in materials.items() if item["status"] != "available"],
            **common,
        )

    return {
        "version": "1.1.0",
        "ticker": ticker,
        "status": score["status"],
        "missing_reason": score.get("missing_reason"),
        **common,
        "sector": sector,
        "formulas": FORMULAS,
        "raw_inputs": inputs,
        "net_cash": net_cash,
        "ncr": {
            "display": ncr_display,
            "calculation": ncr_calculation,
            "calculation_cap_pct": calculation["ncr_calculation_cap_pct"],
            "cap_applied": cap_applied,
        },
        "per_cn": per_cn,
        "roic": {
            "display": roic_display,
            "dpup_calculation": roic_dpup_input,
            "negative_floor_applied": roic_value is not None and roic_value < 0,
        },
        "dpup": dpup,
        "score_materials": materials,
        "cash_value_score": score,
        "independent_from_existing_scores": True,
    }
