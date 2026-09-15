from __future__ import annotations

from typing import Any

from v11_foundation import V11_CONFIG


LABELS = {
    "main_candidate": "🔥 本命候補",
    "early_candidate": "💎 先回り候補",
    "momentum_candidate": "🚀 モメンタム候補",
}


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _condition(
    condition: str,
    label: str,
    status: str,
    actual_value: Any,
    *,
    operator: str | None = None,
    threshold: Any = None,
    source_metric: str | None = None,
    reason: str | None = None,
) -> dict:
    return {
        "condition": condition,
        "label": label,
        "status": status,
        "actual_value": actual_value,
        "operator": operator,
        "threshold": threshold,
        "source_metric": source_metric,
        "reason": reason,
    }


def _minimum(condition: str, label: str, value: Any, threshold: float, source: str) -> dict:
    actual = _number(value)
    if actual is None:
        return _condition(condition, label, "missing", value, operator=">=", threshold=threshold, source_metric=source, reason="required_value_unavailable")
    return _condition(condition, label, "matched" if actual >= threshold else "failed", actual, operator=">=", threshold=threshold, source_metric=source)


def _range(condition: str, label: str, value: Any, minimum: float, maximum: float, source: str) -> dict:
    actual = _number(value)
    threshold = {"min": minimum, "max": maximum}
    if actual is None:
        return _condition(condition, label, "missing", value, operator="between", threshold=threshold, source_metric=source, reason="required_value_unavailable")
    return _condition(condition, label, "matched" if minimum <= actual <= maximum else "failed", actual, operator="between", threshold=threshold, source_metric=source)


def _boolean(condition: str, label: str, value: Any, source: str, *, missing_reason: str = "required_value_unavailable") -> dict:
    if not isinstance(value, bool):
        return _condition(condition, label, "missing", value, operator="is true", threshold=True, source_metric=source, reason=missing_reason)
    return _condition(condition, label, "matched" if value else "failed", value, operator="is true", threshold=True, source_metric=source)


def _trace(key: str, conditions: list[dict], *, forced_state: str | None = None) -> dict:
    matched = [item for item in conditions if item["status"] == "matched"]
    failed = [item for item in conditions if item["status"] == "failed"]
    missing = [item for item in conditions if item["status"] == "missing"]
    state = forced_state or ("not_matched" if failed else "insufficient_data" if missing else "matched")
    return {
        "key": key,
        "label": LABELS[key],
        "state": state,
        "matched_conditions": matched,
        "failed_conditions": failed,
        "missing_conditions": missing,
    }


def _or_group(condition: str, label: str, alternatives: list[dict], source: str) -> dict:
    statuses = [item["status"] for item in alternatives]
    if "matched" in statuses:
        status = "matched"
        reason = None
    elif statuses and all(value == "failed" for value in statuses):
        status = "failed"
        reason = "all_alternatives_failed"
    else:
        status = "missing"
        reason = "no_match_and_one_or_more_alternatives_missing"
    return _condition(
        condition,
        label,
        status,
        {item["condition"]: {"status": item["status"], "actual_value": item.get("actual_value")} for item in alternatives},
        operator="any",
        threshold=True,
        source_metric=source,
        reason=reason,
    )


def _market_ready(momentum: dict) -> dict:
    market = momentum.get("market") or {}
    required = ("price_return_5d_pct", "price_return_20d_pct", "volume_ratio_20d")
    missing = [key for key in required if _number(market.get(key)) is None]
    if _number(market.get("rs_63d_pct")) is None and _number(market.get("rs_126d_pct")) is None:
        missing.append("rs_63d_pct_or_rs_126d_pct")
    return _condition(
        "required_market_data",
        "必要なMarketデータ取得済み",
        "matched" if not missing else "missing",
        {key: market.get(key) for key in (*required, "rs_63d_pct", "rs_126d_pct")},
        operator="all required and one RS available",
        threshold={"required": list(required), "either": ["rs_63d_pct", "rs_126d_pct"]},
        source_metric="momentum.market",
        reason=None if not missing else "missing:" + ",".join(missing),
    )


def _main_trace(growth: Any, change: Any, momentum: dict, forecast: dict, cash: dict) -> dict:
    cfg = V11_CONFIG["classification"]["thresholds"]["main_candidate"]
    conditions = [
        _minimum("growth_score", "Growth", growth, cfg["growth_min"], "growth.result.score"),
        _minimum("change_score", "Change", change, cfg["change_min"], "change.result.score"),
        _minimum("momentum_score", "Momentum", momentum.get("score"), cfg["momentum_min"], "momentum.score"),
        _market_ready(momentum),
    ]
    cash_score = cash.get("cash_value_score") or {}
    cash_value = _number(cash_score.get("value")) if cash_score.get("status") == "available" else None
    upward_known = bool(forecast.get("available"))
    upward = bool(forecast.get("is_upward_revision")) if upward_known else None
    alternatives = [
        _boolean("upward_revision", "上方修正", upward, "forecast_revision"),
        _boolean("tdnet_important_disclosure", "TDnet重要開示", None, "tdnet", missing_reason="tdnet_auto_fetch_off"),
        _minimum("cash_value_score", "Cash Value Score", cash_value, cfg["cash_value_score_min"], "cash_value_v1_1.cash_value_score"),
    ]
    conditions.append(_or_group(
        "main_catalyst",
        "上方修正・TDnet重要開示・Cash Valueのいずれか",
        alternatives,
        "forecast_revision|tdnet|cash_value_v1_1.cash_value_score",
    ))
    return _trace("main_candidate", conditions)


def _early_trace(growth: Any, change: Any, momentum: dict, mispricing: Any, cash: dict, growth_quality: dict) -> dict:
    cfg = V11_CONFIG["classification"]["thresholds"]["early_candidate"]
    ncr = ((cash.get("ncr") or {}).get("display") or {})
    dpup = cash.get("dpup") or {}
    comparison = ((growth_quality.get("cagr_vs_per") or {}).get("comparison") or {})
    conditions = [
        _minimum("growth_score", "Growth", growth, cfg["growth_min"], "growth.result.score"),
        _minimum("change_score", "Change", change, cfg["change_min"], "change.result.score"),
        _range("momentum_range", "Momentum", momentum.get("score"), cfg["momentum_min"], cfg["momentum_max"], "momentum.score"),
        _minimum("ncr", "NCR", ncr.get("value") if ncr.get("status") == "available" else None, cfg["ncr_min_pct"], "cash_value_v1_1.ncr.display.value"),
        _minimum("dpup", "DPUP", dpup.get("value") if dpup.get("status") == "available" else None, cfg["dpup_min"], "cash_value_v1_1.dpup.value"),
        _boolean("five_year_cagr_gt_per", "5年売上高CAGR > PER", comparison.get("value") if comparison.get("status") == "available" else None, "growth_quality_v1_1.cagr_vs_per.comparison"),
        _minimum("mispricing_score", "Mispricing", mispricing, cfg["mispricing_min"], "mispricing.score"),
    ]
    blocking_codes = set(V11_CONFIG["classification"]["early_candidate_blocking_warning_codes"])
    warnings = [item for item in growth_quality.get("warning_flags") or [] if item.get("code") in blocking_codes]
    limit = int(cfg["blocking_warning_count"])
    conditions.append(_condition(
        "growth_quality_warning_limit",
        "Growth Quality重大警告数",
        "matched" if len(warnings) < limit else "failed",
        [item.get("code") for item in warnings],
        operator="count <",
        threshold=limit,
        source_metric="growth_quality_v1_1.warning_flags",
        reason=None if len(warnings) < limit else "multiple_growth_quality_warnings",
    ))
    return _trace("early_candidate", conditions)


def _momentum_trace(momentum: dict, forecast: dict) -> dict:
    cfg = V11_CONFIG["classification"]["thresholds"]["momentum_candidate"]
    market = momentum.get("market") or {}
    rs63 = _number(market.get("rs_63d_pct"))
    rs126 = _number(market.get("rs_126d_pct"))
    if rs63 is None and rs126 is None:
        rs_condition = _condition("relative_strength", "RS 63日または126日", "missing", {"rs_63d_pct": rs63, "rs_126d_pct": rs126}, operator="either >=", threshold=cfg["relative_strength_min_pct"], source_metric="momentum.market", reason="both_relative_strength_values_unavailable")
    else:
        rs_condition = _condition("relative_strength", "RS 63日または126日", "matched" if any(value is not None and value >= float(cfg["relative_strength_min_pct"]) for value in (rs63, rs126)) else "failed", {"rs_63d_pct": rs63, "rs_126d_pct": rs126}, operator="either >=", threshold=cfg["relative_strength_min_pct"], source_metric="momentum.market")
    upward_known = bool(forecast.get("available"))
    upward = bool(forecast.get("is_upward_revision")) if upward_known else None
    catalyst = _or_group(
        "momentum_catalyst",
        "上方修正またはTDnet重要開示",
        [
            _boolean("upward_revision", "上方修正", upward, "forecast_revision"),
            _boolean("tdnet_important_disclosure", "TDnet重要開示", None, "tdnet", missing_reason="tdnet_auto_fetch_off"),
        ],
        "forecast_revision|tdnet",
    )
    return _trace("momentum_candidate", [
        _minimum("momentum_score", "Momentum", momentum.get("score"), cfg["momentum_min"], "momentum.score"),
        _minimum("volume_ratio", "出来高比率", market.get("volume_ratio_20d"), cfg["volume_ratio_min"], "momentum.market.volume_ratio_20d"),
        rs_condition,
        catalyst,
    ])


def _overheat_trace(momentum: dict, mispricing: Any, cash: dict, growth_quality: dict) -> dict:
    cfg = V11_CONFIG["classification"]["thresholds"]["overheat_risk"]
    market = momentum.get("market") or {}
    gate = _minimum("momentum_gate", "Momentum過熱ゲート", momentum.get("score"), cfg["momentum_gate_min"], "momentum.score")
    ret5, ret20 = _number(market.get("price_return_5d_pct")), _number(market.get("price_return_20d_pct"))
    if ret5 is None and ret20 is None:
        price = _condition("short_term_price_rise", "短期株価上昇", "missing", {"5d": ret5, "20d": ret20}, operator="5d or 20d >=", threshold={"5d": cfg["price_return_5d_min_pct"], "20d": cfg["price_return_20d_min_pct"]}, source_metric="momentum.market", reason="short_term_returns_unavailable")
    else:
        price = _condition("short_term_price_rise", "短期株価上昇", "matched" if (ret5 is not None and ret5 >= cfg["price_return_5d_min_pct"]) or (ret20 is not None and ret20 >= cfg["price_return_20d_min_pct"]) else "failed", {"5d": ret5, "20d": ret20}, operator="5d or 20d >=", threshold={"5d": cfg["price_return_5d_min_pct"], "20d": cfg["price_return_20d_min_pct"]}, source_metric="momentum.market")
    mispricing_value = _number(mispricing)
    low_mispricing = _condition("low_mispricing", "Mispricing低位", "missing" if mispricing_value is None else "matched" if mispricing_value <= cfg["mispricing_max"] else "failed", mispricing_value, operator="<=", threshold=cfg["mispricing_max"], source_metric="mispricing.score", reason="mispricing_unavailable" if mispricing_value is None else None)
    per_metric = ((growth_quality.get("cagr_vs_per") or {}).get("per") or {})
    per_value = _number(per_metric.get("value")) if per_metric.get("status") == "available" else None
    per_cn_metric = cash.get("per_cn") or {}
    per_cn_value = _number(per_cn_metric.get("value")) if per_cn_metric.get("status") == "available" else None
    if per_value is None and per_cn_value is None:
        valuation = _condition("high_valuation", "PERまたはPER(CN)高位", "missing", {"per": per_value, "per_cn": per_cn_value}, operator="either >=", threshold={"per": cfg["per_min"], "per_cn": cfg["per_cn_min"]}, source_metric="growth_quality_v1_1|cash_value_v1_1", reason="valuation_metrics_unavailable")
    else:
        valuation = _condition("high_valuation", "PERまたはPER(CN)高位", "matched" if (per_value is not None and per_value >= cfg["per_min"]) or (per_cn_value is not None and per_cn_value >= cfg["per_cn_min"]) else "failed", {"per": per_value, "per_cn": per_cn_value}, operator="either >=", threshold={"per": cfg["per_min"], "per_cn": cfg["per_cn_min"]}, source_metric="growth_quality_v1_1|cash_value_v1_1")
    comparison = ((growth_quality.get("cagr_vs_per") or {}).get("comparison") or {})
    cagr_below = _condition("cagr_below_per", "5年売上高CAGR < PER", "missing" if comparison.get("status") != "available" else "matched" if comparison.get("value") is False else "failed", comparison.get("value"), operator="is false", threshold=False, source_metric="growth_quality_v1_1.cagr_vs_per.comparison", reason=comparison.get("missing_reason") if comparison.get("status") != "available" else None)
    signals = [price, low_mispricing, valuation, cagr_below]
    matched_points = sum(item["status"] == "matched" for item in signals)
    evaluable = sum(item["status"] != "missing" for item in signals)
    missing_points = sum(item["status"] == "missing" for item in signals)
    required_points = int(cfg["required_points"])
    minimum_evaluable = int(cfg["minimum_evaluable_signals"])
    if matched_points >= required_points and evaluable >= minimum_evaluable:
        point_state = "matched"
    elif matched_points + missing_points < required_points:
        point_state = "failed"
    else:
        point_state = "missing"
    if gate["status"] == "failed" or point_state == "failed":
        state = "not_matched"
    elif gate["status"] == "missing" or point_state == "missing":
        state = "insufficient_data"
    else:
        state = "matched"
    trace = _trace("main_candidate", [gate, *signals], forced_state=state)
    trace["key"] = "overheat_risk"
    trace["label"] = "⚠️ 過熱注意"
    trace["point_summary"] = {"matched_points": matched_points, "required_points": cfg["required_points"], "evaluable_signals": evaluable, "minimum_evaluable_signals": cfg["minimum_evaluable_signals"]}
    return trace


def classify_v11(
    ticker: str,
    *,
    growth_result: dict | None,
    change_result: dict | None,
    momentum_result: dict | None,
    mispricing_result: dict | None,
    forecast_revision: dict | None,
    cash_value: dict | None,
    growth_quality: dict | None,
    coverage: dict | None,
) -> dict:
    growth_result, change_result, momentum = growth_result or {}, change_result or {}, momentum_result or {}
    mispricing_result, forecast = mispricing_result or {}, forecast_revision or {}
    cash, growth_quality, coverage = cash_value or {}, growth_quality or {}, coverage or {}
    traces = {
        "main_candidate": _main_trace(growth_result.get("score"), change_result.get("score"), momentum, forecast, cash),
        "early_candidate": _early_trace(growth_result.get("score"), change_result.get("score"), momentum, mispricing_result.get("score"), cash, growth_quality),
        "momentum_candidate": _momentum_trace(momentum, forecast),
    }
    priority = V11_CONFIG["classification"]["primary_priority"]
    selected = next((key for key in priority if traces[key]["state"] == "matched"), None)
    primary_state = "matched" if selected else "insufficient_data" if any(item["state"] == "insufficient_data" for item in traces.values()) else "not_matched"
    primary = {"state": primary_state, "key": selected, "label": LABELS.get(selected), "priority": priority}

    tag_cfg = V11_CONFIG["classification"]["thresholds"]["secondary_tags"]
    secondary_tags = []
    def add_tag(code: str, label: str, value: Any, source: str) -> None:
        secondary_tags.append({"code": code, "label": label, "evidence": {"actual_value": value, "source_metric": source}})
    if _number(momentum.get("score")) is not None and momentum["score"] >= tag_cfg["momentum_strong_min"]:
        add_tag("momentum_strong", "🚀 モメンタム強", momentum["score"], "momentum.score")
    if forecast.get("available") and forecast.get("is_upward_revision"):
        add_tag("upward_revision", "上方修正", forecast.get("guidance_revision_pct"), "forecast_revision")
    cash_score = cash.get("cash_value_score") or {}
    if cash_score.get("status") == "available" and _number(cash_score.get("value")) is not None and cash_score["value"] >= tag_cfg["cash_value_high_min"]:
        add_tag("cash_value_high", "Cash Value高", cash_score["value"], "cash_value_v1_1.cash_value_score")
    growth_quality_score = growth_quality.get("growth_quality_score") or {}
    if growth_quality_score.get("status") == "available" and _number(growth_quality_score.get("value")) is not None and growth_quality_score["value"] >= tag_cfg["growth_quality_high_min"]:
        add_tag("growth_quality_high", "Growth Quality高", growth_quality_score["value"], "growth_quality_v1_1.growth_quality_score")

    overheat = _overheat_trace(momentum, mispricing_result.get("score"), cash, growth_quality)
    risk_flags = []
    if overheat["state"] == "matched":
        risk_flags.append({"code": "overheat_review", "label": "⚠️ 過熱注意", "source": "classification.overheat_risk", "evidence": overheat["point_summary"]})
    warning_labels = {
        "GROSS_MARGIN_DETERIORATING": "⚠️ 粗利益率悪化",
        "OPERATING_MARGIN_DETERIORATING": "⚠️ 営業利益率悪化",
        "GROWTH_DECELERATING": "⚠️ 成長減速",
        "OPERATING_CASH_FLOW_NON_POSITIVE": "⚠️ 営業CF弱い",
    }
    for warning in growth_quality.get("warning_flags") or []:
        if warning.get("code") in warning_labels:
            risk_flags.append({"code": warning["code"].lower(), "label": warning_labels[warning["code"]], "source": "growth_quality_v1_1.warning_flags", "evidence": warning.get("evidence")})
    for sanity in coverage.get("sanity_flags") or []:
        risk_flags.append({"code": sanity.get("code"), "label": "⚠️ ROIC sanity review", "source": "coverage_v1_1.sanity_flags", "evidence": sanity, "affects_score": False})

    return {
        "version": "1.1.0",
        "ticker": ticker,
        "status": primary_state,
        "primary_candidate": primary,
        "secondary_tags": secondary_tags,
        "risk_flags": risk_flags,
        "classifications": traces,
        "overheat_risk": overheat,
        "growth_quality_warning_flags": growth_quality.get("warning_flags") or [],
        "roic_sanity_flags": coverage.get("sanity_flags") or [],
        "coverage_policy": {
            "overall_coverage_used_as_investment_score": False,
            "overall_coverage_pct": (coverage.get("overall_coverage") or {}).get("percentage"),
            "required_item_availability_checked_directly": True,
        },
        "tdnet_policy": {"auto_fetch_enabled": False, "missing_tdnet_not_scored_as_zero": True},
        "independent_from_existing_scores": True,
    }
