from __future__ import annotations

import datetime
import math
from dataclasses import asdict, is_dataclass
from typing import Any

import pandas as pd
from fastapi import APIRouter, HTTPException

from deep_fcf_top5 import build_deep_fcf_top5, evaluate_deep_fcf_rows
from entry_opportunity import build_entry_opportunity
from multiple_expansion import (
    build_point_in_time_financials,
    calculate_fcf_engine,
    calculate_market_confirmation,
    calculate_mex_components,
    calculate_multiple_normalization,
    calculate_price_driver,
    calculate_valuation_multiples,
    classify_fcf_quality,
    classify_mex_state,
    confirm_state_transitions,
    run_multiple_expansion_pipeline,
)
from multiple_expansion_screener import (
    build_daily_screener,
    candidate_only as filter_candidates,
)
from pretrade import evaluate_pretrade
from short_cover import (
    apply_optimizer_condition,
    build_priority_alerts,
    evaluate_entry_followup,
    evaluate_entry_hunter,
)
from stock_action_status import evaluate_stock_action_status
from stock_priority_board import build_daily_priority_board
from v3_contracts import (
    ActionStatusRequest,
    ActionStatusData,
    DeepFcfData,
    DeepFcfRequest,
    EntryRequest,
    MeDailyRequest,
    MultipleExpansionRequest,
    PreTradeRequest,
    PriorityBoardRequest,
    PriorityBoardRow,
    ShortCoverRequest,
    V3Response,
    available_response,
    unavailable_response,
)


router = APIRouter(prefix="/v3", tags=["v3"])


def _date(value: str | None, field_name: str) -> datetime.date | None:
    if value is None or value == "":
        return None
    try:
        return datetime.date.fromisoformat(value[:10])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{field_name} must be YYYY-MM-DD") from exc


def _json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (datetime.date, datetime.datetime, pd.Timestamp)):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            return _json_safe(value.item())
        except (ValueError, TypeError):
            pass
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if value is pd.NA:
        return None
    return value


def _records(frame: pd.DataFrame | None) -> list[dict[str, Any]]:
    if frame is None or frame.empty:
        return []
    normalized = frame.astype(object).where(pd.notna(frame), None)
    return _json_safe(normalized.to_dict(orient="records"))


def _row_coverage(*rows: dict[str, Any]) -> float:
    present = sum(1 for row in rows if row)
    return present / len(rows) if rows else 0.0


def _run_me_pipeline(
    market: pd.DataFrame,
    financial: pd.DataFrame | None,
    config: dict[str, Any],
) -> pd.DataFrame:
    """Run the canonical pipeline with a pandas 3 grouping-key compatibility path."""
    try:
        return run_multiple_expansion_pipeline(market, financial, config=config)
    except KeyError as exc:
        if exc.args != ("ticker",):
            raise

    cfg = config or {}
    if financial is not None and not financial.empty:
        if "available_date" in financial.columns:
            frame = build_point_in_time_financials(financial, market)
        else:
            keys = [
                key for key in ("trade_date", "ticker")
                if key in market.columns and key in financial.columns
            ]
            if len(keys) != 2:
                raise ValueError(
                    "financial_rows must contain either available_date or both trade_date and ticker"
                )
            frame = market.merge(financial, on=keys, how="left", suffixes=("", "_fin"))
    else:
        frame = market.copy()

    frame = calculate_valuation_multiples(frame)
    frame["_v3_ticker_identity"] = frame["ticker"]
    frame = calculate_multiple_normalization(
        frame,
        lookback_days=int(cfg.get("lookback_days", 1260)),
        min_history_days=int(cfg.get("min_history_days", 504)),
        core_ema_span=int(cfg.get("core_ema_span", 40)),
    )
    if "ticker" not in frame.columns:
        frame["ticker"] = frame.pop("_v3_ticker_identity")
    else:
        frame = frame.drop(columns=["_v3_ticker_identity"], errors="ignore")
    frame = calculate_fcf_engine(frame)
    frame = classify_fcf_quality(frame)
    frame = calculate_market_confirmation(frame)
    frame = calculate_mex_components(frame)
    frame["state_raw"] = frame.apply(classify_mex_state, axis=1)
    frame = confirm_state_transitions(
        frame,
        confirmation_days=int(cfg.get("confirmation_days", 2)),
        cooldown_days=int(cfg.get("cooldown_days", 20)),
    )
    return calculate_price_driver(
        frame,
        horizon_days=int(cfg.get("price_driver_horizon_days", 252)),
    )


@router.post("/action-status", response_model=V3Response[ActionStatusData])
def action_status(req: ActionStatusRequest):
    rows = (req.portfolio_row, req.me_row, req.short_row, req.live_entry)
    if not any(rows):
        return unavailable_response(
            source=req.source,
            as_of=req.as_of,
            reason="V3 ACTION STATUSの入力データが未連携です",
            expected=4,
            received=0,
        )
    result = evaluate_stock_action_status(
        portfolio_row=req.portfolio_row,
        me_row=req.me_row,
        short_row=req.short_row,
        live_entry=req.live_entry,
        earnings_date=_date(req.earnings_date, "earnings_date"),
        today=_date(req.today, "today"),
    )
    received = sum(1 for row in rows if row)
    data = _json_safe(result)
    if req.ticker:
        data = {"ticker": req.ticker, **data}
    return available_response(
        source=req.source,
        as_of=req.as_of,
        data=data,
        coverage=_row_coverage(*rows),
        expected=4,
        received=received,
    )


@router.post("/priority-board", response_model=V3Response[list[PriorityBoardRow]])
def priority_board(req: PriorityBoardRequest):
    if not req.portfolio_rows:
        return unavailable_response(
            source=req.source,
            as_of=req.as_of,
            reason="Priority Boardに必要な保有・監視銘柄データが未連携です",
            expected=1,
            received=0,
        )
    result = build_daily_priority_board(
        portfolio_rows=req.portfolio_rows,
        me_rows=req.me_rows,
        short_rows=req.short_rows,
        live_entry_rows=req.live_entry_rows,
        today=_date(req.today, "today"),
        include_passed=req.include_passed,
    )
    source_count = sum(bool(rows) for rows in (
        req.portfolio_rows,
        req.me_rows,
        req.short_rows,
        req.live_entry_rows,
    ))
    return available_response(
        source=req.source,
        as_of=req.as_of,
        data=_json_safe(result),
        coverage=source_count / 4,
        expected=4,
        received=source_count,
    )


@router.post("/entry", response_model=V3Response[dict[str, Any]])
def entry(req: EntryRequest):
    if req.mode == "opportunity":
        if not req.candidate:
            return unavailable_response(
                source=req.source,
                as_of=req.as_of,
                reason="Entry Opportunityの候補データが未連携です",
            )
        result = build_entry_opportunity(req.candidate, req.entry_snapshot)
        coverage = 1.0 if req.entry_snapshot else 0.55
    else:
        if not req.inputs:
            return unavailable_response(
                source=req.source,
                as_of=req.as_of,
                reason="Entry Hunterの価格データが未取得です",
            )
        evaluator = evaluate_entry_hunter if req.mode == "hunter" else evaluate_entry_followup
        try:
            result = evaluator(**req.inputs)
        except TypeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        coverage = 0.0 if result.get("status") == "⚪ NO DATA" else 1.0
    return available_response(
        source=req.source,
        as_of=req.as_of,
        data=_json_safe(result),
        coverage=coverage,
    )


@router.post("/short-cover", response_model=V3Response[list[dict[str, Any]]])
def short_cover(req: ShortCoverRequest):
    if not req.current_rows:
        return unavailable_response(
            source=req.source,
            as_of=req.as_of,
            reason="Short Coverのランキングデータが未連携です",
        )
    current = pd.DataFrame(req.current_rows)
    if req.optimizer_condition is not None:
        current = apply_optimizer_condition(current, req.optimizer_condition)
    result = build_priority_alerts(
        current,
        pd.DataFrame(req.promotion_rows) if req.promotion_rows else None,
        limit=req.limit,
        validation_ready=req.validation_ready,
    )
    received = 1 + int(bool(req.promotion_rows)) + int(req.optimizer_condition is not None)
    return available_response(
        source=req.source,
        as_of=req.as_of,
        data=_records(result),
        coverage=received / 3,
        expected=3,
        received=received,
    )


@router.post("/pre-trade", response_model=V3Response[dict[str, Any]])
def pre_trade(req: PreTradeRequest):
    if not req.technical:
        return unavailable_response(
            source=req.source,
            as_of=req.as_of,
            reason="Pre-Tradeに必要なテクニカルデータが未取得です",
        )
    result = evaluate_pretrade(
        req.technical,
        entry=req.entry,
        stop=req.stop,
        target=req.target,
        capital=req.capital,
        risk_percent=req.risk_percent,
        earnings_days=req.earnings_days,
        entry_hunter=req.entry_hunter,
        pattern_flags=req.pattern_flags,
        lot_size=req.lot_size,
    )
    required = (req.entry, req.stop, req.target, req.capital)
    received = sum(value is not None for value in required)
    return available_response(
        source=req.source,
        as_of=req.as_of,
        data=_json_safe(result),
        coverage=received / len(required),
        expected=len(required),
        received=received,
    )


@router.post("/deep-fcf", response_model=V3Response[DeepFcfData])
def deep_fcf(req: DeepFcfRequest):
    if not req.rows:
        return unavailable_response(
            source=req.source,
            as_of=req.as_of,
            reason="Deep FCFの計算済みMEデータが未連携です",
        )
    frame = pd.DataFrame(req.rows)
    ranking = build_deep_fcf_top5(
        frame,
        limit=req.limit,
        min_coverage=req.min_coverage,
        min_score=req.min_score,
        min_quality_grade=req.min_quality_grade,
    )
    data: dict[str, Any] = {"ranking": _records(ranking)}
    if req.include_evaluations:
        data["evaluations"] = _records(evaluate_deep_fcf_rows(
            frame,
            min_coverage=req.min_coverage,
            min_score=req.min_score,
            min_quality_grade=req.min_quality_grade,
        ))
    return available_response(
        source=req.source,
        as_of=req.as_of,
        data=data,
        coverage=1.0,
        expected=len(req.rows),
        received=len(req.rows),
    )


@router.post("/me", response_model=V3Response[list[dict[str, Any]]])
def multiple_expansion(req: MultipleExpansionRequest):
    if not req.market_rows:
        return unavailable_response(
            source=req.source,
            as_of=req.as_of,
            reason="Multiple Expansionの市場履歴データが未連携です",
        )
    frame = _run_me_pipeline(
        pd.DataFrame(req.market_rows),
        pd.DataFrame(req.financial_rows) if req.financial_rows else None,
        config=req.config,
    )
    records = _records(frame)
    if not req.include_components:
        keep = {
            "trade_date", "ticker", "company_name", "fcf_engine_score",
            "fcf_engine_coverage", "mex_score", "mex_coverage", "state_raw",
            "state_confirmed", "ignition_event", "price_driver_label",
        }
        records = [{key: value for key, value in row.items() if key in keep} for row in records]
    source_count = 1 + int(bool(req.financial_rows))
    return available_response(
        source=req.source,
        as_of=req.as_of,
        data=records,
        coverage=source_count / 2,
        expected=2,
        received=source_count,
    )


@router.post("/me-daily", response_model=V3Response[list[dict[str, Any]]])
def me_daily(req: MeDailyRequest):
    if not req.history_rows:
        return unavailable_response(
            source=req.source,
            as_of=req.as_of,
            reason="ME Daily Screenerの履歴データが未連携です",
        )
    result = build_daily_screener(pd.DataFrame(req.history_rows))
    if req.candidates_only:
        result = filter_candidates(result)
    return available_response(
        source=req.source,
        as_of=req.as_of,
        data=_records(result),
        coverage=1.0,
        expected=len(req.history_rows),
        received=len(req.history_rows),
    )
