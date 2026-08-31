from pydantic import BaseModel, Field

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
