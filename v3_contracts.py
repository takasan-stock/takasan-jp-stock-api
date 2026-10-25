from __future__ import annotations

from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, Field


CANONICAL_SOURCE = "takasan-stock/stock-analyzer@main"
V3_CONTRACT_VERSION = "v3-contract-1"
V3_ENGINE_MODULES = {
    "action_status": "861ee394c293984cebe0b483e5888a28390b841e",
    "priority_board": "1e4c668bc7ff442cb639437ab10e73093b9762bd",
    "deep_fcf": "ab45e222054b6fd6f3c649ae40e9fe379bf932fc",
    "multiple_expansion": "4db656b13be1e511204e5938650bb81ab896f212",
    "me_daily": "228f3e95be237d2986200c500f8a4a56c9efe295",
    "short_cover": "5c13babf828d9d1ba0115aab22bc686ebb647298",
    "entry": "9de3152e368d3ec2927bd79f1745266216f5384c",
    "pre_trade": "c16756737623feb2441496b7d9864d4bd5976597",
}
DataT = TypeVar("DataT")


class V3DataQuality(BaseModel):
    status: Literal["complete", "partial", "reported", "unavailable"]
    expected: int | None = None
    received: int | None = None


class V3Response(BaseModel, Generic[DataT]):
    available: bool
    source: str
    as_of: str | None = None
    coverage: float | None = None
    data_quality: V3DataQuality
    unavailable_reason: str | None = None
    data: DataT | None = None


class ActionStatusData(BaseModel):
    ticker: str | None = None
    stage: Literal["DATA_CHECK", "RESEARCH", "MONITOR", "ENTRY_MONITOR", "PRETRADE", "EVENT_CHECK"]
    label: str
    icon: str
    readiness_score: float
    confidence: Literal["HIGH", "MEDIUM", "LOW"]
    summary: str
    reasons: list[str]
    blockers: list[str]
    next_page: str
    next_label: str
    next_icon: str


class PriorityBoardRow(BaseModel):
    ticker: str
    name: str
    portfolio_status: str
    stage: str
    stage_label: str
    stage_icon: str
    readiness: float
    confidence: str
    summary: str
    lead_reason: str
    lead_blocker: str
    next_page: str
    next_label: str
    next_icon: str
    mex_score: float | None = None
    sw_decision: str | None = None
    entry_status: str | None = None
    rank: int


class DeepFcfData(BaseModel):
    ranking: list[dict[str, Any]]
    evaluations: list[dict[str, Any]] | None = None


class V3RequestBase(BaseModel):
    as_of: str | None = None
    source: str = CANONICAL_SOURCE


class ActionStatusRequest(V3RequestBase):
    ticker: str | None = None
    portfolio_row: dict[str, Any] = Field(default_factory=dict)
    me_row: dict[str, Any] = Field(default_factory=dict)
    short_row: dict[str, Any] = Field(default_factory=dict)
    live_entry: dict[str, Any] = Field(default_factory=dict)
    earnings_date: str | None = None
    today: str | None = None


class PriorityBoardRequest(V3RequestBase):
    portfolio_rows: list[dict[str, Any]] = Field(default_factory=list)
    me_rows: list[dict[str, Any]] = Field(default_factory=list)
    short_rows: list[dict[str, Any]] = Field(default_factory=list)
    live_entry_rows: list[dict[str, Any]] = Field(default_factory=list)
    today: str | None = None
    include_passed: bool = False


class EntryRequest(V3RequestBase):
    mode: Literal["hunter", "followup", "opportunity"] = "hunter"
    inputs: dict[str, Any] = Field(default_factory=dict)
    candidate: dict[str, Any] = Field(default_factory=dict)
    entry_snapshot: dict[str, Any] | None = None


class ShortCoverRequest(V3RequestBase):
    current_rows: list[dict[str, Any]] = Field(default_factory=list)
    promotion_rows: list[dict[str, Any]] = Field(default_factory=list)
    optimizer_condition: dict[str, Any] | None = None
    limit: int = Field(default=5, ge=1, le=100)
    validation_ready: bool | None = None


class PreTradeRequest(V3RequestBase):
    technical: dict[str, Any] = Field(default_factory=dict)
    entry: float | None = None
    stop: float | None = None
    target: float | None = None
    capital: float | None = None
    risk_percent: float = 1.0
    earnings_days: int | None = None
    entry_hunter: dict[str, Any] | None = None
    pattern_flags: dict[str, bool] | None = None
    lot_size: int = Field(default=100, ge=1)


class DeepFcfRequest(V3RequestBase):
    rows: list[dict[str, Any]] = Field(default_factory=list)
    limit: int = Field(default=5, ge=1, le=100)
    min_coverage: float = Field(default=0.60, ge=0.0, le=1.0)
    min_score: float = Field(default=55.0, ge=0.0, le=100.0)
    min_quality_grade: str = "C"
    include_evaluations: bool = True


class MultipleExpansionRequest(V3RequestBase):
    market_rows: list[dict[str, Any]] = Field(default_factory=list)
    financial_rows: list[dict[str, Any]] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)
    include_components: bool = True


class MeDailyRequest(V3RequestBase):
    history_rows: list[dict[str, Any]] = Field(default_factory=list)
    candidates_only: bool = False


def unavailable_response(
    *,
    source: str,
    as_of: str | None,
    reason: str,
    expected: int | None = None,
    received: int | None = None,
) -> dict[str, Any]:
    coverage = None
    if expected is not None and expected > 0 and received is not None:
        coverage = min(1.0, max(0.0, received / expected))
    return {
        "available": False,
        "source": source,
        "as_of": as_of,
        "coverage": coverage,
        "data_quality": {
            "status": "unavailable",
            "expected": expected,
            "received": received,
        },
        "unavailable_reason": reason,
        "data": None,
    }


def available_response(
    *,
    source: str,
    as_of: str | None,
    data: Any,
    coverage: float | None,
    expected: int | None = None,
    received: int | None = None,
) -> dict[str, Any]:
    if coverage is not None:
        coverage = min(1.0, max(0.0, float(coverage)))
    quality = "complete" if coverage == 1.0 else "partial"
    if coverage is None:
        quality = "reported"
    return {
        "available": True,
        "source": source,
        "as_of": as_of,
        "coverage": coverage,
        "data_quality": {
            "status": quality,
            "expected": expected,
            "received": received,
        },
        "unavailable_reason": None,
        "data": data,
    }
