import os
from typing import Iterable

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from models import GrowthRequest, ChangeRequest, MispricingRequest, DCFRequest
from scoring import score_growth, score_change, score_mispricing, run_dcf
from provider import get_company_snapshot


app = FastAPI(
    title="たかさん日本株分析 v2 API",
    version="0.3.0",
    description="ChatGPT Sites 接続用の日本株分析API",
)

origins = [x.strip() for x in os.getenv("ALLOWED_ORIGINS", "*").split(",") if x.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    return {
        "name": "たかさん日本株分析 v2 API",
        "version": "0.3.0",
        "status": "ok",
        "docs": "/docs",
    }


@app.get("/health")
def health():
    return {"status": "ok", "version": "0.3.0"}


@app.post("/score/growth")
def growth(req: GrowthRequest):
    return score_growth(req)


@app.post("/score/change")
def change(req: ChangeRequest):
    return score_change(req)


@app.post("/score/mispricing")
def mispricing(req: MispricingRequest):
    return score_mispricing(req)


@app.post("/dcf")
def dcf(req: DCFRequest):
    return run_dcf(req)


def _missing(data: dict, keys: Iterable[str]) -> list[str]:
    return [key for key in keys if data.get(key) is None]


def _dcf_growth_assumption(data: dict) -> float:
    candidates = [
        data.get("revenue_growth_pct"),
        data.get("operating_profit_growth_pct"),
    ]
    values = [float(v) for v in candidates if v is not None]
    if not values:
        return 0.0

    # 単年度の高成長をそのまま永続化しないため、5年DCF用は -5%〜15% に制限。
    raw = sum(values) / len(values)
    return round(max(-5.0, min(15.0, raw)), 2)


def _composite_score(parts: list[tuple[float, float]]) -> float | None:
    if not parts:
        return None
    weight_sum = sum(weight for _, weight in parts)
    if weight_sum <= 0:
        return None
    return round(sum(score * weight for score, weight in parts) / weight_sum, 1)


def _rank(score: float | None) -> str | None:
    if score is None:
        return None
    if score >= 90:
        return "S"
    if score >= 80:
        return "A"
    if score >= 65:
        return "B"
    if score >= 50:
        return "C"
    return "D"


@app.get("/analyze/{ticker}")
def analyze(ticker: str):
    ticker = ticker.strip().upper().replace(".T", "")
    if len(ticker) != 4 or not ticker.isdigit():
        raise HTTPException(status_code=400, detail="4桁の証券コードを指定してください。")

    data = get_company_snapshot(ticker)
    if data is None:
        raise HTTPException(
            status_code=404,
            detail="実データを取得できませんでした。銘柄コードまたは外部データ接続を確認してください。",
        )

    growth_required = [
        "revenue_growth_pct",
        "operating_profit_growth_pct",
        "eps_growth_pct",
        "operating_margin_pct",
    ]
    growth_missing = _missing(data, growth_required)

    growth_result = None
    if not growth_missing:
        growth_result = score_growth(
            GrowthRequest(
                revenue_growth_pct=data["revenue_growth_pct"],
                operating_profit_growth_pct=data["operating_profit_growth_pct"],
                eps_growth_pct=data["eps_growth_pct"],
                operating_margin_pct=data["operating_margin_pct"],
            )
        )

    change_required = [
        "latest_growth_pct",
        "previous_growth_pct",
        "margin_change_points",
    ]
    change_missing = _missing(data, change_required)

    change_result = None
    if not change_missing:
        # 会社予想修正率が取得できない場合はChange Score内部だけ中立値0を使う。
        # JSON上では guidance_revision_available=False として欠損を明示する。
        change_result = score_change(
            ChangeRequest(
                latest_growth_pct=data["latest_growth_pct"],
                previous_growth_pct=data["previous_growth_pct"],
                margin_change_points=data["margin_change_points"],
                guidance_revision_pct=data.get("guidance_revision_pct") or 0.0,
            )
        )

    dcf_result = None
    dcf_assumptions = None
    if (
        data.get("base_free_cash_flow") is not None
        and data["base_free_cash_flow"] > 0
        and data.get("shares_outstanding") is not None
        and data["shares_outstanding"] > 0
    ):
        growth_assumption = _dcf_growth_assumption(data)
        dcf_assumptions = {
            "base_free_cash_flow": data["base_free_cash_flow"],
            "growth_rate_pct": growth_assumption,
            "discount_rate_pct": 8.0,
            "terminal_growth_rate_pct": 2.0,
            "years": 5,
            "net_debt": data.get("net_debt") or 0.0,
            "shares_outstanding": data["shares_outstanding"],
        }
        dcf_result = run_dcf(DCFRequest(**dcf_assumptions))

    mispricing_result = None
    fair_value = None
    if dcf_result and not dcf_result.get("error"):
        fair_value = dcf_result.get("fair_value_per_share")

    if (
        fair_value is not None
        and fair_value > 0
        and data.get("market_price") is not None
        and data["market_price"] > 0
        and growth_result is not None
    ):
        mispricing_result = score_mispricing(
            MispricingRequest(
                fair_value=fair_value,
                market_price=data["market_price"],
                growth_score=growth_result["score"],
                data_coverage_pct=data.get("data_coverage_pct", 0),
            )
        )

    composite_parts = []
    if growth_result is not None:
        composite_parts.append((growth_result["score"], 0.50))
    if change_result is not None:
        composite_parts.append((change_result["score"], 0.30))
    if mispricing_result is not None:
        composite_parts.append((mispricing_result["score"], 0.20))

    ai_score = _composite_score(composite_parts)

    return {
        "ticker": ticker,
        "company_name": data.get("company_name"),
        "fiscal_period": data.get("fiscal_period"),
        "data_status": data.get("data_status"),
        "data_source": data.get("data_source"),
        "data_coverage_pct": data.get("data_coverage_pct"),
        "missing_fields": data.get("missing_fields", []),
        "market_price": data.get("market_price"),
        "market_cap": data.get("market_cap"),
        "metrics": {
            "revenue_growth_pct": data.get("revenue_growth_pct"),
            "operating_profit_growth_pct": data.get("operating_profit_growth_pct"),
            "eps_growth_pct": data.get("eps_growth_pct"),
            "operating_margin_pct": data.get("operating_margin_pct"),
            "margin_change_points": data.get("margin_change_points"),
            "guidance_revision_pct": data.get("guidance_revision_pct"),
            "guidance_revision_available": data.get("guidance_revision_available", False),
            "free_cash_flow": data.get("base_free_cash_flow"),
            "net_debt": data.get("net_debt"),
            "shares_outstanding": data.get("shares_outstanding"),
        },
        "growth": {
            "result": growth_result,
            "missing_fields": growth_missing,
        },
        "change": {
            "result": change_result,
            "missing_fields": change_missing,
            "guidance_note": (
                None
                if data.get("guidance_revision_available")
                else "会社予想修正率は取得できていないため、Change Score内部では中立値0を使用。"
            ),
        },
        "dcf": {
            "result": dcf_result,
            "assumptions": dcf_assumptions,
            "note": (
                "DCFはYahoo Finance経由のFCF等を使った簡易モデル。決算資料・有報での再確認を推奨。"
                if dcf_result is not None
                else "正のFCFまたは発行株式数を取得できないためDCFを計算していません。"
            ),
        },
        "mispricing": mispricing_result,
        "ai_score": ai_score,
        "rank": _rank(ai_score),
        "score_weights": {
            "growth": 0.50,
            "change": 0.30,
            "mispricing": 0.20,
            "note": "取得できたスコアだけで重みを再正規化します。",
        },
        "note": "外部データに欠損がある場合は架空値を生成せず、missing_fieldsに明示します。",
    }
