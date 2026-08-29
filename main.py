import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from models import (
    GrowthRequest, GrowthResponse,
    ChangeRequest, ChangeResponse,
    MispricingRequest, MispricingResponse,
    DCFRequest, DCFResponse,
    AnalyzeResponse,
)
from services.scoring import (
    score_growth,
    score_change,
    score_mispricing,
    run_dcf,
)
from providers.mock import MockProvider

app = FastAPI(
    title="たかさん日本株分析 v2 API",
    version="0.1.0",
    description="ChatGPT Sites から呼び出すための日本株分析APIスターター",
)

allowed_origins = [
    x.strip()
    for x in os.getenv("ALLOWED_ORIGINS", "*").split(",")
    if x.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

provider = MockProvider()


@app.get("/")
def root():
    return {
        "name": "たかさん日本株分析 v2 API",
        "version": "0.1.0",
        "status": "ok",
        "docs": "/docs",
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/score/growth", response_model=GrowthResponse)
def growth(req: GrowthRequest):
    return score_growth(req)


@app.post("/score/change", response_model=ChangeResponse)
def change(req: ChangeRequest):
    return score_change(req)


@app.post("/score/mispricing", response_model=MispricingResponse)
def mispricing(req: MispricingRequest):
    return score_mispricing(req)


@app.post("/dcf", response_model=DCFResponse)
def dcf(req: DCFRequest):
    return run_dcf(req)


@app.get("/analyze/{ticker}", response_model=AnalyzeResponse)
def analyze(ticker: str):
    """
    1銘柄の一括分析。
    現在はデータ接続確認用の MockProvider を使用。
    実運用では providers/ を実データ取得クラスへ差し替える。
    """
    ticker = ticker.strip().upper()
    if len(ticker.replace(".T", "")) != 4:
        raise HTTPException(status_code=400, detail="4桁の証券コードを指定してください。")

    data = provider.get_company_snapshot(ticker)
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

    # 一括AIスコアの初期版。Momentum/Valuationが未接続のため、
    # Growth 50% + Change 50% として暫定計算。
    ai_score = round(
        growth_result.score * 0.5 + change_result.score * 0.5, 1
    )

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

    return AnalyzeResponse(
        ticker=ticker.replace(".T", ""),
        company_name=data["company_name"],
        fiscal_period=data["fiscal_period"],
        data_status="demo_connector",
        revenue_growth_pct=data["revenue_growth_pct"],
        operating_profit_growth_pct=data["operating_profit_growth_pct"],
        eps_growth_pct=data["eps_growth_pct"],
        operating_margin_pct=data["operating_margin_pct"],
        growth_score=growth_result.score,
        change_score=change_result.score,
        ai_score=ai_score,
        rank=rank,
        note="実データ接続前の接続テスト用。未登録銘柄には架空値を返しません。",
    )
