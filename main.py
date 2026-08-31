from fastapi import FastAPI, HTTPException
from models import GrowthRequest, ChangeRequest, MispricingRequest, DCFRequest
from scoring import score_growth, score_change, score_mispricing, calc_dcf, calc_dcf_scenarios
from provider import get_company_snapshot

app = FastAPI(title="たかさん日本株分析 v2 API", version="0.5.0",
              description="Growth / Change / DCF 3シナリオ / Mispricing 日本株分析API")

@app.get("/")
def root():
    return {"name": "たかさん日本株分析 v2 API", "version": "0.5.0", "status": "ok", "docs": "/docs"}

@app.get("/health")
def health():
    return {"status": "ok", "version": "0.5.0"}

@app.post("/score/growth")
def growth(req: GrowthRequest):
    return score_growth(**req.model_dump())

@app.post("/score/change")
def change(req: ChangeRequest):
    return score_change(**req.model_dump())

@app.post("/score/mispricing")
def mispricing(req: MispricingRequest):
    return score_mispricing(**req.model_dump())

@app.post("/dcf")
def dcf(req: DCFRequest):
    try:
        return calc_dcf(**req.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/analyze/{ticker}")
def analyze(ticker: str):
    data = get_company_snapshot(ticker)
    if not data:
        raise HTTPException(status_code=404, detail="この銘柄の実データを取得できませんでした。")

    m = data["metrics"]
    growth = score_growth(
        m["revenue_growth_pct"], m["operating_profit_growth_pct"],
        m["eps_growth_pct"], m["operating_margin_pct"]
    )
    change = score_change(
        m["latest_growth_pct"], m["previous_growth_pct"],
        m["margin_change_points"], m.get("guidance_revision_pct") or 0
    )

    dcf = {"result": None, "scenarios": None, "note": None}
    required = ["free_cash_flow", "net_debt", "shares_outstanding"]
    if all(m.get(k) is not None for k in required) and m["free_cash_flow"] > 0 and m["shares_outstanding"] > 0:
        scenarios = calc_dcf_scenarios(
            m["free_cash_flow"], m["net_debt"], m["shares_outstanding"],
            data.get("market_price"), years=5
        )
        base_fair = scenarios["scenarios"]["base"]["fair_value_per_share"]
        dcf = {
            "result": scenarios["scenarios"]["base"],
            "scenarios": scenarios,
            "note": "弱気・標準・強気の3シナリオ。前提は投資判断時に個別確認してください。"
        }
        mispricing = score_mispricing(
            base_fair, data["market_price"], growth["score"], data.get("data_coverage_pct", 100)
        ) if data.get("market_price") else None
    else:
        mispricing = None
        dcf["note"] = "DCFに必要な実データが不足、またはFCFが正でないため未計算です。"

    # 既存AIスコアとの互換性を保つ簡易合成
    available = [growth["score"], change["score"]]
    if mispricing:
        available.append(mispricing["score"])
    ai_score = round(sum(available) / len(available), 1)
    rank = "S" if ai_score >= 90 else "A" if ai_score >= 75 else "B" if ai_score >= 60 else "C" if ai_score >= 45 else "D"

    return {
        **data,
        "growth": {"result": growth},
        "change": {"result": change, "guidance_available": m.get("guidance_revision_available", False)},
        "dcf": dcf,
        "mispricing": mispricing,
        "ai_score": ai_score,
        "rank": rank,
        "score_weights": {"note": "v0.5.0では取得可能スコアを均等合成。今後ウェイト調整可能。"},
        "note": None,
    }
