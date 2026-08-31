import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from models import GrowthRequest, ChangeRequest, MispricingRequest, DCFRequest
from scoring import score_growth, score_change, score_mispricing, run_dcf
from provider import get_company_snapshot

app = FastAPI(title="たかさん日本株分析 v2 API", version="0.4.0", description="ChatGPT Sites 接続用の日本株分析API")
origins = [x.strip() for x in os.getenv("ALLOWED_ORIGINS","*").split(",") if x.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False, allow_methods=["*"], allow_headers=["*"])

@app.get("/")
def root():
    return {"name":"たかさん日本株分析 v2 API","version":"0.4.0","status":"ok","docs":"/docs"}

@app.get("/health")
def health(): return {"status":"ok"}

@app.post("/score/growth")
def growth(req: GrowthRequest): return score_growth(req)

@app.post("/score/change")
def change(req: ChangeRequest): return score_change(req)

@app.post("/score/mispricing")
def mispricing(req: MispricingRequest): return score_mispricing(req)

@app.post("/dcf")
def dcf(req: DCFRequest): return run_dcf(req)

def _missing_required(metrics, names):
    return [n for n in names if metrics.get(n) is None]

@app.get("/analyze/{ticker}")
def analyze(ticker: str):
    ticker = ticker.strip().upper().replace(".T","")
    if len(ticker) != 4 or not ticker.isdigit():
        raise HTTPException(status_code=400, detail="4桁の証券コードを指定してください。")
    data = get_company_snapshot(ticker)
    if data is None:
        raise HTTPException(status_code=404, detail="この銘柄の財務データを取得できませんでした。架空値は生成しません。")
    m = data["metrics"]

    growth_missing = _missing_required(m,["revenue_growth_pct","operating_profit_growth_pct","eps_growth_pct","operating_margin_pct"])
    growth_result = None if growth_missing else score_growth(GrowthRequest(
        revenue_growth_pct=m["revenue_growth_pct"],
        operating_profit_growth_pct=m["operating_profit_growth_pct"],
        eps_growth_pct=m["eps_growth_pct"],
        operating_margin_pct=m["operating_margin_pct"],
    ))

    change_missing = _missing_required(m,["latest_growth_pct","previous_growth_pct","margin_change_points"])
    change_result = None if change_missing else score_change(ChangeRequest(
        latest_growth_pct=m["latest_growth_pct"],
        previous_growth_pct=m["previous_growth_pct"],
        margin_change_points=m["margin_change_points"],
        guidance_revision_pct=m.get("guidance_revision_pct"),
        sign_flip_penalty=bool(m.get("sign_flip_penalty",False)),
    ))

    dcf_result = None
    dcf_assumptions = None
    dcf_note = None
    fcf, shares, net_debt = m.get("free_cash_flow"), m.get("shares_outstanding"), m.get("net_debt")

    if fcf is not None and fcf > 0 and shares is not None and shares > 0:
        growth_for_dcf = 5.0
        if m.get("revenue_growth_pct") is not None:
            growth_for_dcf = max(-5.0, min(15.0, m["revenue_growth_pct"]))
        dcf_assumptions = {
            "base_free_cash_flow":fcf,
            "growth_rate_pct":round(growth_for_dcf,2),
            "discount_rate_pct":8.0,
            "terminal_growth_rate_pct":1.0,
            "years":5,
            "net_debt":net_debt or 0.0,
            "shares_outstanding":shares,
            "assumption_policy":"自動計算用の暫定仮定。投資判断時は個別に見直してください。"
        }
        dcf_result = run_dcf(DCFRequest(**{k:v for k,v in dcf_assumptions.items() if k!="assumption_policy"}))
    else:
        dcf_note = "正のFCFまたは発行株式数を取得できないためDCFを計算していません。"

    mispricing_result = None
    if dcf_result and not dcf_result.get("error") and dcf_result.get("fair_value_per_share") and data.get("market_price") and growth_result:
        mispricing_result = score_mispricing(MispricingRequest(
            fair_value=dcf_result["fair_value_per_share"],
            market_price=data["market_price"],
            growth_score=growth_result["score"],
            data_coverage_pct=data["data_coverage_pct"],
        ))

    items, weights = [], []
    if growth_result: items.append(growth_result["score"]); weights.append(0.50)
    if change_result: items.append(change_result["score"]); weights.append(0.30)
    if mispricing_result: items.append(mispricing_result["score"]); weights.append(0.20)
    ai_score = round(sum(s*w for s,w in zip(items,weights))/sum(weights),1) if items else None
    rank = None if ai_score is None else "S" if ai_score>=90 else "A" if ai_score>=80 else "B" if ai_score>=65 else "C" if ai_score>=50 else "D"

    return {
        "ticker":ticker,
        "company_name":data["company_name"],
        "fiscal_period":data["fiscal_period"],
        "comparison_period":data.get("comparison_period"),
        "data_status":data["data_status"],
        "data_source":data["data_source"],
        "data_retrieved_at_utc":data.get("data_retrieved_at_utc"),
        "data_coverage_pct":data["data_coverage_pct"],
        "missing_fields":data["missing_fields"],
        "market_price":data["market_price"],
        "metrics":m,
        "growth":{"result":growth_result,"missing_fields":growth_missing},
        "change":{"result":change_result,"missing_fields":change_missing,"guidance_note":None if m.get("guidance_revision_available") else "会社予想修正率は取得できていないため、Change Score内部では中立値を使用。"},
        "dcf":{"result":dcf_result,"assumptions":dcf_assumptions,"note":dcf_note},
        "mispricing":mispricing_result,
        "ai_score":ai_score,
        "rank":rank,
        "quality_flags":{
            "guidance_revision_available":bool(m.get("guidance_revision_available",False)),
            "dcf_available":dcf_result is not None
        },
        "note":"外部データに欠損がある場合は架空値を生成せず、missing_fieldsに明示します。"
    }
