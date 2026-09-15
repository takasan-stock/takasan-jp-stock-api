from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


MetricStatus = Literal["available", "not_acquired", "not_calculable", "not_applicable", "short_history", "insufficient_data"]
ClassificationState = Literal["matched", "not_matched", "insufficient_data"]
TdnetCategory = Literal[
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
    "other",
]
TdnetImportance = Literal["low", "medium", "high", "critical"]
TdnetRecordStatus = Literal["active", "correction", "corrected", "deleted"]
JQuantsReflectionStatus = Literal[
    "not_applicable",
    "pending_comparison",
    "reflected",
    "not_reflected",
]


class MetricEnvelope(BaseModel):
    value: Any | None = None
    status: MetricStatus
    source: str | None = None
    source_field: str | None = None
    fiscal_period: str | None = None
    retrieved_at: str | None = None
    missing_reason: str | None = None


class ConditionEvidence(BaseModel):
    condition: str
    actual_value: Any | None = None
    threshold: Any | None = None
    source_metric: str | None = None
    reason: str | None = None


class ClassificationTrace(BaseModel):
    state: ClassificationState
    matched_conditions: list[ConditionEvidence] = Field(default_factory=list)
    failed_conditions: list[ConditionEvidence] = Field(default_factory=list)
    missing_conditions: list[ConditionEvidence] = Field(default_factory=list)


class JQuantsComparisonInfo(BaseModel):
    eligible: bool
    comparison_kind: str | None = None
    comparison_key: str | None = None
    tdnet_content_fingerprint: str | None = None
    jquants_record_id: str | None = None
    jquants_updated_at: datetime | None = None
    jquants_content_fingerprint: str | None = None
    status: JQuantsReflectionStatus
    reason: str


class TdnetDisclosureMetadata(BaseModel):
    disclosure_id: str = Field(min_length=1, max_length=160)
    disclosed_at: datetime
    ticker: str = Field(pattern=r"^[0-9A-Z]{4}$")
    company_name: str = Field(min_length=1, max_length=240)
    title: str = Field(min_length=1, max_length=1000)
    category: TdnetCategory
    category_label: str
    importance: TdnetImportance
    official_material_url: str = Field(pattern=r"^https://")
    record_status: TdnetRecordStatus = "active"
    supersedes_disclosure_id: str | None = None
    retrieved_at: datetime
    matched_rule_keyword: str | None = None
    deduplication_key: str
    jquants_comparison: JQuantsComparisonInfo

    @field_validator("ticker", mode="before")
    @classmethod
    def normalize_ticker(cls, value: object) -> str:
        return str(value).strip().upper().removeprefix("TSE:").removesuffix(".T")

class GrowthRequest(BaseModel):
    revenue_growth_pct: float
    operating_profit_growth_pct: float
    eps_growth_pct: float
    operating_margin_pct: float

class ChangeRequest(BaseModel):
    latest_growth_pct: float
    previous_growth_pct: float
    margin_change_points: float
    guidance_revision_pct: float | None = None
    sign_flip_penalty: bool = False

class MispricingRequest(BaseModel):
    fair_value: float = Field(gt=0)
    market_price: float = Field(gt=0)
    growth_score: float = Field(ge=0, le=100)
    data_coverage_pct: float = Field(default=100, ge=0, le=100)

class DCFRequest(BaseModel):
    base_free_cash_flow: float
    growth_rate_pct: float
    discount_rate_pct: float
    terminal_growth_rate_pct: float
    years: int = Field(default=5, ge=1, le=20)
    net_debt: float = 0
    shares_outstanding: float | None = Field(default=None, gt=0)
