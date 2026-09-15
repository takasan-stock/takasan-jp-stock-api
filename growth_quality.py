from __future__ import annotations

from typing import Any

from v11_foundation import V11_CONFIG


FORMULAS = {
    "cagr": "((latest / oldest) ** (1 / years) - 1) * 100",
    "one_year_growth": "(latest / previous - 1) * 100",
    "gross_margin": "gross_profit / revenue * 100",
    "operating_margin": "operating_profit / revenue * 100",
    "effective_tax_rate": "tax_provision / pretax_income",
    "roic": "operating_profit * (1 - effective_tax_rate) / (total_equity + total_debt - cash_and_equivalents) * 100",
}


def envelope(
    value: float | bool | str | None = None,
    status: str = "available",
    *,
    source: str | list[str] | None = None,
    fiscal_period: str | None = None,
    periods: list[str] | None = None,
    formula: str | None = None,
    missing_reason: str | None = None,
    judgment_reason: str | None = None,
    **metadata: Any,
) -> dict:
    return {
        "value": value,
        "status": status,
        "source": source,
        "fiscal_period": fiscal_period,
        "periods": periods or [],
        "formula": formula,
        "missing_reason": missing_reason,
        "judgment_reason": judgment_reason,
        **metadata,
    }


def _unavailable(reason: str, status: str = "not_acquired", **metadata: Any) -> dict:
    return envelope(None, status, missing_reason=reason, **metadata)


def _series(history: dict, name: str) -> dict:
    return (history.get("series") or {}).get(name) or {
        "status": "not_acquired",
        "points": [],
        "missing_reason": "series_not_available",
    }


def _points(history: dict, name: str) -> list[dict]:
    item = _series(history, name)
    points = [
        {"period": point.get("period"), "value": float(point["value"])}
        for point in item.get("points") or []
        if point.get("period") and isinstance(point.get("value"), (int, float)) and not isinstance(point.get("value"), bool)
    ]
    return sorted(points, key=lambda point: point["period"])


def _source_metadata(item: dict, periods: list[str]) -> dict:
    return {
        "source": item.get("source"),
        "fiscal_period": periods[-1] if periods else None,
        "periods": periods,
        "source_field": item.get("source_field"),
        "consolidation": item.get("consolidation"),
        "currency": item.get("currency"),
        "retrieved_at": item.get("retrieved_at"),
    }


def _growth_metric(history: dict, name: str, years: int) -> dict:
    item = _series(history, name)
    points = _points(history, name)
    required = years + 1
    if not points:
        return _unavailable(
            item.get("missing_reason") or "series_not_available",
            "not_acquired",
            **_source_metadata(item, []),
        )
    if len(points) < required:
        return _unavailable(
            f"requires_{required}_annual_points_but_found_{len(points)}",
            "short_history",
            **_source_metadata(item, [point["period"] for point in points]),
        )
    selected = points[-required:]
    oldest, latest = selected[0]["value"], selected[-1]["value"]
    periods = [point["period"] for point in selected]
    formula = FORMULAS["one_year_growth"] if years == 1 else FORMULAS["cagr"]
    if oldest <= 0 or latest < 0:
        return _unavailable(
            "growth_requires_positive_oldest_and_nonnegative_latest",
            "not_calculable",
            formula=formula,
            input_points=selected,
            **_source_metadata(item, periods),
        )
    value = (latest / oldest - 1) * 100 if years == 1 else ((latest / oldest) ** (1 / years) - 1) * 100
    return envelope(
        value,
        formula=formula,
        input_points=selected,
        judgment_reason=f"{periods[0]}から{periods[-1]}までの実績値で算出",
        **_source_metadata(item, periods),
    )


def _margin_metric(history: dict, profit_name: str, formula_name: str) -> dict:
    revenue_item = _series(history, "revenue")
    profit_item = _series(history, profit_name)
    revenue = {point["period"]: point["value"] for point in _points(history, "revenue")}
    profit = {point["period"]: point["value"] for point in _points(history, profit_name)}
    periods = sorted(set(revenue) & set(profit))
    if not periods:
        reason = profit_item.get("missing_reason") or "revenue_and_profit_periods_do_not_overlap"
        return _unavailable(reason, source=[revenue_item.get("source"), profit_item.get("source")], formula=FORMULAS[formula_name])
    series = []
    for period in periods:
        if revenue[period] == 0:
            continue
        series.append({"period": period, "value": profit[period] / revenue[period] * 100})
    if not series:
        return _unavailable("revenue_is_zero_for_all_matching_periods", "not_calculable", formula=FORMULAS[formula_name])
    latest = series[-1]
    previous = series[-2] if len(series) >= 2 else None
    change = latest["value"] - previous["value"] if previous else None
    threshold = float(V11_CONFIG["growth_quality"]["margin_trend_threshold_points"])
    trend = "short_history"
    reason = "利益率の前年差を判定するには2期必要です。"
    if change is not None:
        trend = "improving" if change >= threshold else "deteriorating" if change <= -threshold else "stable"
        reason = f"前年差{change:+.2f}ポイント、判定閾値±{threshold:.2f}ポイント"
    sources = sorted({source for source in (revenue_item.get("source"), profit_item.get("source")) if source})
    consolidations = {value for value in (revenue_item.get("consolidation"), profit_item.get("consolidation")) if value}
    return envelope(
        latest["value"],
        source=sources,
        fiscal_period=latest["period"],
        periods=[point["period"] for point in series],
        formula=FORMULAS[formula_name],
        judgment_reason=reason,
        series=series,
        input_points=[
            {"period": period, "revenue": revenue[period], profit_name: profit[period]}
            for period in periods
            if revenue[period] != 0
        ],
        latest_change_points=change,
        trend=trend,
        consolidation=next(iter(consolidations)) if len(consolidations) == 1 else None,
        retrieved_at=profit_item.get("retrieved_at") or revenue_item.get("retrieved_at"),
    )


def _operating_cash_flow(history: dict) -> dict:
    item = _series(history, "operating_cash_flow")
    points = _points(history, "operating_cash_flow")
    if not points:
        return _unavailable(item.get("missing_reason") or "operating_cash_flow_not_available", source=item.get("source"))
    latest = points[-1]
    previous = points[-2] if len(points) >= 2 else None
    return envelope(
        latest["value"],
        source=item.get("source"),
        fiscal_period=latest["period"],
        periods=[point["period"] for point in points],
        formula="latest annual operating cash flow",
        judgment_reason="営業CFはプラス" if latest["value"] > 0 else "営業CFは0以下",
        series=points,
        latest_change_amount=latest["value"] - previous["value"] if previous else None,
        positive=latest["value"] > 0,
        consolidation=item.get("consolidation"),
        currency=item.get("currency"),
        retrieved_at=item.get("retrieved_at"),
    )


def _period_point(history: dict, name: str, period: str) -> tuple[dict, float | None]:
    item = _series(history, name)
    point = next((point for point in _points(history, name) if point["period"] == period), None)
    return item, point["value"] if point else None


def _roic(history: dict, fiscal_period: str | None) -> dict:
    if not fiscal_period:
        return _unavailable("latest_fiscal_period_not_available", "not_calculable", formula=FORMULAS["roic"])
    names = ("operating_profit", "pretax_income", "tax_provision", "total_equity", "total_debt", "cash_and_equivalents")
    items = {}
    values = {}
    for name in names:
        items[name], values[name] = _period_point(history, name, fiscal_period)
    missing = [name for name, value in values.items() if value is None]
    sources = sorted({item.get("source") for item in items.values() if item.get("source")})
    if missing:
        return _unavailable(
            "missing_same_period_inputs:" + ",".join(missing),
            "not_acquired",
            source=sources,
            fiscal_period=fiscal_period,
            periods=[fiscal_period],
            formula=FORMULAS["roic"],
            inputs=values,
        )
    consolidations = {item.get("consolidation") for item in items.values()}
    currencies = {item.get("currency") for item in items.values()}
    if len(consolidations) != 1 or None in consolidations or len(currencies) != 1 or None in currencies:
        return _unavailable(
            "roic_input_basis_or_currency_mismatch",
            "not_calculable",
            source=sources,
            fiscal_period=fiscal_period,
            periods=[fiscal_period],
            formula=FORMULAS["roic"],
            inputs=values,
        )
    if values["pretax_income"] <= 0:
        return _unavailable("effective_tax_rate_requires_positive_pretax_income", "not_calculable", source=sources, fiscal_period=fiscal_period, formula=FORMULAS["roic"], inputs=values)
    effective_tax_rate = values["tax_provision"] / values["pretax_income"]
    if effective_tax_rate < 0 or effective_tax_rate > 1:
        return _unavailable("effective_tax_rate_out_of_range", "not_calculable", source=sources, fiscal_period=fiscal_period, formula=FORMULAS["roic"], inputs=values)
    invested_capital = values["total_equity"] + values["total_debt"] - values["cash_and_equivalents"]
    if invested_capital <= 0:
        return _unavailable("invested_capital_not_positive", "not_calculable", source=sources, fiscal_period=fiscal_period, formula=FORMULAS["roic"], inputs=values)
    nopat = values["operating_profit"] * (1 - effective_tax_rate)
    return envelope(
        nopat / invested_capital * 100,
        source=sources,
        fiscal_period=fiscal_period,
        periods=[fiscal_period],
        formula=FORMULAS["roic"],
        judgment_reason="同一期の営業利益、実効税率、投下資本から算出",
        inputs={**values, "effective_tax_rate": effective_tax_rate, "nopat": nopat, "invested_capital": invested_capital},
        consolidation=next(iter(consolidations)),
        currency=next(iter(currencies)),
    )


def _acceleration(revenue_5y: dict, revenue_3y: dict, revenue_1y: dict, evaluation_state: str) -> dict:
    if evaluation_state != "standard_5y":
        return _unavailable(
            f"five_year_comparison_not_available:{evaluation_state}",
            "short_history",
            formula="compare revenue CAGR 5y, CAGR 3y, and growth 1y",
        )
    metrics = {"five_year_pct": revenue_5y.get("value"), "three_year_pct": revenue_3y.get("value"), "one_year_pct": revenue_1y.get("value")}
    if any(not isinstance(value, (int, float)) for value in metrics.values()):
        return _unavailable("one_or_more_growth_horizons_not_calculable", "not_calculable", inputs=metrics)
    threshold = float(V11_CONFIG["growth_quality"]["acceleration_threshold_points"])
    if metrics["one_year_pct"] >= metrics["three_year_pct"] + threshold and metrics["three_year_pct"] >= metrics["five_year_pct"] + threshold:
        value = "accelerating"
    elif metrics["one_year_pct"] <= metrics["three_year_pct"] - threshold and metrics["three_year_pct"] <= metrics["five_year_pct"] - threshold:
        value = "decelerating"
    else:
        value = "maintaining"
    return envelope(
        value,
        source=sorted({source for metric in (revenue_5y, revenue_3y, revenue_1y) for source in ([metric.get("source")] if isinstance(metric.get("source"), str) else metric.get("source") or []) if source}),
        fiscal_period=revenue_1y.get("fiscal_period"),
        periods=revenue_5y.get("periods") or [],
        consolidation=revenue_1y.get("consolidation"),
        formula="1y vs 3y CAGR vs 5y CAGR",
        judgment_reason=f"5年{metrics['five_year_pct']:.2f}%、3年{metrics['three_year_pct']:.2f}%、1年{metrics['one_year_pct']:.2f}%を閾値{threshold:.2f}ptで比較",
        inputs=metrics,
    )


def _linear_score(value: float, full_score_pct: float) -> float:
    if full_score_pct <= 0:
        raise ValueError("full_score_pct must be positive")
    return round(max(0.0, min(100.0, value / full_score_pct * 100)), 2)


def _score_growth_quality(metrics: dict[str, dict]) -> tuple[dict, dict[str, dict]]:
    config = V11_CONFIG["growth_quality"]["score"]
    weights = config["weights"]
    trend_scores = config["trend_scores"]
    components: dict[str, dict] = {}

    def add(name: str, item: dict, normalized_score: float | None) -> None:
        components[name] = {
            "raw_value": item.get("value"),
            "normalized_score": normalized_score,
            "weight": weights[name],
            "status": item.get("status", "not_acquired"),
            "missing_reason": item.get("missing_reason"),
        }

    revenue_5y = metrics["revenue_cagr_5y"]
    add("revenue_cagr_5y", revenue_5y, _linear_score(revenue_5y["value"], config["growth_full_score_pct"]) if revenue_5y["status"] == "available" else None)
    eps_5y = metrics["eps_cagr_5y"]
    add("eps_cagr_5y", eps_5y, _linear_score(eps_5y["value"], config["growth_full_score_pct"]) if eps_5y["status"] == "available" else None)
    for name in ("growth_trend", "gross_margin_trend", "operating_margin_trend"):
        item = metrics[name]
        add(name, item, trend_scores.get(item.get("value")) if item["status"] == "available" else None)
    ocf = metrics["operating_cash_flow"]
    add("operating_cash_flow", ocf, 100.0 if ocf.get("positive") else 0.0 if ocf["status"] == "available" else None)
    roic = metrics["roic"]
    add("roic", roic, _linear_score(roic["value"], config["roic_full_score_pct"]) if roic["status"] == "available" else None)
    comparison = metrics["revenue_cagr_5y_gt_per"]
    add("revenue_cagr_5y_gt_per", comparison, 100.0 if comparison.get("value") is True else 0.0 if comparison["status"] == "available" else None)

    if revenue_5y["status"] != "available":
        score = _unavailable(
            revenue_5y.get("missing_reason") or "primary_metric_unavailable",
            revenue_5y["status"],
            profile="growth_quality_v1",
            weights=weights,
        )
        return score, components

    active = [item for item in components.values() if item["status"] == "available" and item["normalized_score"] is not None]
    active_weight = sum(float(item["weight"]) for item in active)
    if active_weight <= 0:
        return _unavailable("no_score_components_available", "not_calculable", profile="growth_quality_v1", weights=weights), components
    value = sum(float(item["normalized_score"]) * float(item["weight"]) for item in active) / active_weight
    return envelope(round(value, 1), profile="growth_quality_v1", weights=weights, active_weight=active_weight), components


def _warning_flags(metrics: dict[str, dict], evaluation_state: str) -> list[dict]:
    warnings = []

    def add(code: str, severity: str, message: str, evidence: Any = None) -> None:
        warnings.append({"code": code, "severity": severity, "message": message, "evidence": evidence})

    if evaluation_state != "standard_5y":
        add("SHORT_HISTORY", "info", "5年実績が揃っていないため通常企業とは別状態で評価します。", evaluation_state)
    trend = metrics["growth_trend"]
    if trend.get("value") == "decelerating":
        add("GROWTH_DECELERATING", "warning", "5年・3年・1年の比較で売上成長が減速しています。", trend.get("inputs"))
    gross_margin = metrics["gross_margin_trend"]
    if gross_margin.get("value") == "deteriorating":
        add("GROSS_MARGIN_DETERIORATING", "warning", "粗利益率が前年から悪化しています。", gross_margin.get("latest_change_points"))
    operating_margin = metrics["operating_margin_trend"]
    if operating_margin.get("value") == "deteriorating":
        add("OPERATING_MARGIN_DETERIORATING", "warning", "営業利益率が前年から悪化しています。", operating_margin.get("latest_change_points"))
    ocf = metrics["operating_cash_flow"]
    if ocf["status"] == "available" and ocf.get("positive") is False:
        add("OPERATING_CASH_FLOW_NON_POSITIVE", "warning", "直近の営業キャッシュフローが0以下です。", ocf.get("value"))
    roic = metrics["roic"]
    if roic["status"] == "available" and isinstance(roic.get("value"), (int, float)) and roic["value"] < 0:
        add("ROIC_NEGATIVE", "warning", "ROICがマイナスです。", roic.get("value"))
    elif roic["status"] != "available":
        add("ROIC_UNAVAILABLE", "info", "ROICを算出できる同一期データが揃っていません。", roic.get("missing_reason"))
    comparison = metrics["revenue_cagr_5y_gt_per"]
    if comparison["status"] == "available" and comparison.get("value") is False:
        add("CAGR_NOT_ABOVE_PER", "warning", "5年売上高CAGRがPERを上回っていません。", {"cagr": comparison.get("revenue_cagr_5y_pct"), "per": comparison.get("per")})
    eps = metrics["eps_cagr_5y"]
    if eps["status"] != "available":
        add("EPS_CAGR_UNAVAILABLE", "info", "5年EPS CAGRを補助指標として算出できません。", eps.get("missing_reason"))
    return warnings


def calculate_growth_quality(
    ticker: str,
    history: dict,
    *,
    per: float | None,
    per_source: str,
    retrieved_at: str,
) -> dict:
    revenue_points = _points(history, "revenue")
    count = len(revenue_points)
    evaluation_state = "standard_5y" if count >= 6 else "limited_3y_history" if count >= 4 else "ipo_short_history" if count >= 2 else "insufficient_history"
    revenue_5y = _growth_metric(history, "revenue", 5)
    revenue_3y = _growth_metric(history, "revenue", 3)
    revenue_1y = _growth_metric(history, "revenue", 1)
    eps_5y = _growth_metric(history, "eps", 5)
    overall_status = revenue_5y["status"]
    acceleration = _acceleration(revenue_5y, revenue_3y, revenue_1y, evaluation_state)
    gross_margin = _margin_metric(history, "gross_profit", "gross_margin")
    operating_margin = _margin_metric(history, "operating_profit", "operating_margin")
    operating_cash_flow = _operating_cash_flow(history)
    fiscal_period = revenue_points[-1]["period"] if revenue_points else None
    roic = _roic(history, fiscal_period)

    per_metric = envelope(per, source=per_source, retrieved_at=retrieved_at) if per is not None and per > 0 else _unavailable(
        "per_missing" if per is None else "per_not_positive",
        "not_acquired" if per is None else "not_calculable",
        source=per_source,
    )
    if revenue_5y["status"] != "available":
        cagr_vs_per = _unavailable(revenue_5y.get("missing_reason") or "five_year_revenue_cagr_unavailable", revenue_5y["status"], formula="revenue_cagr_5y > PER")
    elif per_metric["status"] != "available":
        cagr_vs_per = _unavailable(per_metric.get("missing_reason") or "per_unavailable", per_metric["status"], formula="revenue_cagr_5y > PER")
    else:
        matched = revenue_5y["value"] > per_metric["value"]
        cagr_vs_per = envelope(
            matched,
            formula="revenue_cagr_5y > PER",
            judgment_reason=f"5年売上高CAGR {revenue_5y['value']:.2f}% {'>' if matched else '<='} PER {per_metric['value']:.2f}倍",
            revenue_cagr_5y_pct=revenue_5y["value"],
            per=per_metric["value"],
        )

    score_inputs = {
        "revenue_cagr_5y": revenue_5y,
        "eps_cagr_5y": eps_5y,
        "growth_trend": acceleration,
        "gross_margin_trend": envelope(gross_margin.get("trend"), gross_margin["status"], missing_reason=gross_margin.get("missing_reason")),
        "operating_margin_trend": envelope(operating_margin.get("trend"), operating_margin["status"], missing_reason=operating_margin.get("missing_reason")),
        "operating_cash_flow": operating_cash_flow,
        "roic": roic,
        "revenue_cagr_5y_gt_per": cagr_vs_per,
    }
    growth_quality_score, score_components = _score_growth_quality(score_inputs)
    warning_flags = _warning_flags(score_inputs, evaluation_state)

    matched_conditions = []
    failed_conditions = []
    missing_conditions = []
    for condition, metric in (
        ("five_year_revenue_cagr", revenue_5y),
        ("five_year_eps_cagr", eps_5y),
        ("growth_acceleration", acceleration),
        ("gross_margin", gross_margin),
        ("operating_margin", operating_margin),
        ("operating_cash_flow", operating_cash_flow),
        ("roic", roic),
        ("five_year_revenue_cagr_gt_per", cagr_vs_per),
    ):
        evidence = {"condition": condition, "status": metric["status"], "value": metric.get("value"), "reason": metric.get("judgment_reason") or metric.get("missing_reason")}
        if metric["status"] != "available":
            missing_conditions.append(evidence)
        elif condition == "five_year_revenue_cagr_gt_per" and metric.get("value") is False:
            failed_conditions.append(evidence)
        else:
            matched_conditions.append(evidence)

    sources = sorted({item.get("source") for item in (history.get("series") or {}).values() if item.get("source")})
    return {
        "version": "1.1.0",
        "ticker": ticker,
        "status": overall_status,
        "evaluation_state": evaluation_state,
        "history_profile": history.get("history_profile"),
        "missing_reason": None if overall_status == "available" else f"revenue_history_points:{count}",
        "source": sources,
        "fiscal_period": fiscal_period,
        "retrieved_at": retrieved_at,
        "formulas": FORMULAS,
        "primary_metric": "revenue_cagr_5y",
        "auxiliary_metric": "eps_cagr_5y",
        "revenue_growth": {"five_year_cagr": revenue_5y, "three_year_cagr": revenue_3y, "one_year": revenue_1y},
        "eps_growth": {"five_year_cagr": eps_5y},
        "profitability": {"gross_margin": gross_margin, "operating_margin": operating_margin},
        "operating_cash_flow": operating_cash_flow,
        "roic": roic,
        "growth_trend": acceleration,
        "cagr_vs_per": {"revenue_cagr_5y": revenue_5y, "per": per_metric, "comparison": cagr_vs_per},
        "growth_quality_score": growth_quality_score,
        "score_components": score_components,
        "warning_flags": warning_flags,
        "matched_conditions": matched_conditions,
        "failed_conditions": failed_conditions,
        "missing_conditions": missing_conditions,
        "independent_from_existing_growth_score": True,
    }
