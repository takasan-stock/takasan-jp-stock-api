import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from models import GrowthRequest, ChangeRequest, MispricingRequest, DCFRequest
from scoring import score_growth, score_change, score_mispricing, run_dcf
from provider import get_company_snapshot

app = FastAPI(
    title="たかさん日本株分析 v2 API",
    version="0.2.0",
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
        "version": "0.2.0",
        "status": "ok",
        "docs": "/docs",
    }

@app.get("/health")
def health():
    return {"status": "ok"}

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

@app.get("/analyze/{ticker}")
def analyze(ticker: str):
    ticker = ticker.strip().upper().replace(".T", "")
    if len(ticker) != 4 or not ticker.isdigit():
        raise HTTPException(status_code=400, detail="4桁の証券コードを指定してください。")

    data = get_company_snapshot(ticker)
    if data is None:
        raise HTTPException(
            status_code=404,
            detail="この銘柄の実データはまだ接続されていません。架空値は生成しません。",
        )

    growth_result = score_growth(
        GrowthRequest(
            revenue_growth_pct=data["revenue_growth_pct"],
            operating_profit_growth_pct=data["operating_profit_growth_pct"],
            eps_growth_pct=data["eps_growth_pct"],
            operating_margin_pct=data["operating_margin_pct"],
        )
    )

    change_result = score_change(
        ChangeRequest(
            latest_growth_pct=data["latest_growth_pct"],
            previous_growth_pct=data["previous_growth_pct"],
            margin_change_points=data["margin_change_points"],
            guidance_revision_pct=data.get("guidance_revision_pct", 0),
        )
    )

    ai_score = round((growth_result["score"] + change_result["score"]) / 2, 1)

    if ai_score >= 90:
        rank = "S"
    elif ai_score >= 80:
        rank = "A"
    elif ai_score >= 65:
        rank = "B"
    elif ai_score >= 50:
        rank = "C"
    else:
        rank = "D"

    return {
        "ticker": ticker,
        "company_name": data["company_name"],
        "fiscal_period": data["fiscal_period"],
        "data_status": "demo_connector",
        "revenue_growth_pct": data["revenue_growth_pct"],
        "operating_profit_growth_pct": data["operating_profit_growth_pct"],
        "eps_growth_pct": data["eps_growth_pct"],
        "operating_margin_pct": data["operating_margin_pct"],
        "growth_score": growth_result["score"],
        "change_score": change_result["score"],
        "ai_score": ai_score,
        "rank": rank,
        "note": "実データ接続前の接続テスト用。未登録銘柄には架空値を返しません。",
    }
