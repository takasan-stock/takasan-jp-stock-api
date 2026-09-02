import os
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from models import GrowthRequest, ChangeRequest, MispricingRequest, DCFRequest
from scoring import (
    score_growth,
    score_change,
    score_mispricing,
    score_momentum,
    run_dcf,
    build_auto_dcf_assumptions,
    run_dcf_scenarios,
)
from provider import get_company_snapshot


class BatchAnalyzeRequest(BaseModel):
    tickers: list[str] = Field(min_length=1, max_length=100)
    sort_by: str = "momentum"
    min_rank: str | None = None
    only_upward_revision: bool = False


class BatchRankingRequest(BaseModel):
    tickers: list[str] = Field(min_length=1, max_length=100)
    min_rank: str | None = None
    only_upward_revision: bool = False
    limit: int = Field(default=20, ge=1, le=100)


RANK_ORDER = {"S": 5, "A": 4, "B": 3, "C": 2, "D": 1, None: 0}


def _rank_at_least(rank, min_rank):
    if not min_rank:
        return True
    return RANK_ORDER.get(rank, 0) >= RANK_ORDER.get(min_rank.upper(), 0)


def _summary_from_analysis(result):
    forecast = result.get("forecast_revision") or {}
    momentum = result.get("momentum") or {}
    growth = (result.get("growth") or {}).get("result") or {}
    change = (result.get("change") or {}).get("result") or {}
    dcf = result.get("dcf") or {}
    scenarios = dcf.get("scenarios") or {}
    scenario_block = scenarios.get("scenarios") or {}
    base = scenario_block.get("base") or {}
    mispricing = result.get("mispricing") or {}

    return {
        "ticker": result.get("ticker"),
        "company_name": result.get("company_name"),
        "market_price": result.get("market_price"),
        "ai_score": result.get("ai_score"),
        "rank": result.get("rank"),
        "momentum_score": momentum.get("score"),
        "momentum_rank": momentum.get("rank"),
        "momentum_label": momentum.get("label"),
        "growth_score": growth.get("score"),
        "change_score": change.get("score"),
        "guidance_revision_pct": forecast.get("guidance_revision_pct"),
        "is_upward_revision": forecast.get("is_upward_revision"),
        "is_downward_revision": forecast.get("is_downward_revision"),
        "forecast_revision_status": forecast.get("status"),
        "dcf_base_fair_value": base.get("fair_value_per_share"),
        "dcf_valuation_label": scenarios.get("valuation_label"),
        "mispricing_score": mispricing.get("score"),
        "data_coverage_pct": result.get("data_coverage_pct"),
        "missing_fields": result.get("missing_fields"),
    }


def _sort_value(row, sort_by):
    key_map = {
        "momentum": "momentum_score",
        "ai": "ai_score",
        "growth": "growth_score",
        "change": "change_score",
        "revision": "guidance_revision_pct",
        "mispricing": "mispricing_score",
    }
    key = key_map.get(sort_by, "momentum_score")
    value = row.get(key)
    return -10**18 if value is None else value


app = FastAPI(
    title="たかさん日本株分析 v2 API",
    version="1.0.0",
    description="Growth / Change / Momentum v2 + J-Quants会社予想履歴探索強化 / 自動DCF / Mispricing 日本株分析API",
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
        "version": "1.0.0",
        "status": "ok",
        "docs": "/docs",
    }


@app.get("/health")
def health():
    return {"status": "ok", "version": "1.0.0"}


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


def _missing_required(metrics, names):
    return [name for name in names if metrics.get(name) is None]


def _analyze_one(ticker: str):
    ticker = ticker.strip().upper().replace(".T", "")
    if len(ticker) != 4 or not ticker.isdigit():
        raise HTTPException(status_code=400, detail="4桁の証券コードを指定してください。")

    data = get_company_snapshot(ticker)
    if data is None:
        raise HTTPException(
            status_code=404,
            detail="この銘柄の財務データを取得できませんでした。架空値は生成しません。",
        )

    m = data["metrics"]

    growth_missing = _missing_required(
        m,
        ["revenue_growth_pct", "operating_profit_growth_pct", "eps_growth_pct", "operating_margin_pct"],
    )
    growth_result = None
    if not growth_missing:
        growth_result = score_growth(
            GrowthRequest(
                revenue_growth_pct=m["revenue_growth_pct"],
                operating_profit_growth_pct=m["operating_profit_growth_pct"],
                eps_growth_pct=m["eps_growth_pct"],
                operating_margin_pct=m["operating_margin_pct"],
            )
        )

    change_missing = _missing_required(
        m,
        ["latest_growth_pct", "previous_growth_pct", "margin_change_points"],
    )
    change_result = None
    if not change_missing:
        change_result = score_change(
            ChangeRequest(
                latest_growth_pct=m["latest_growth_pct"],
                previous_growth_pct=m["previous_growth_pct"],
                margin_change_points=m["margin_change_points"],
                guidance_revision_pct=m.get("guidance_revision_pct"),
                sign_flip_penalty=bool(m.get("sign_flip_penalty", False)),
            )
        )

    momentum_result = score_momentum(m, growth_result, change_result)

    fcf = m.get("free_cash_flow")
    net_debt = m.get("net_debt")
    shares = m.get("shares_outstanding")
    market_price = data.get("market_price")

    dcf_missing_fields = [
        name for name, value in {
            "free_cash_flow": fcf,
            "net_debt": net_debt,
            "shares_outstanding": shares,
        }.items() if value is None
    ]

    dcf_payload = {
        "result": None,
        "scenarios": None,
        "missing_fields": dcf_missing_fields,
        "note": None,
    }
    mispricing_result = None

    if not dcf_missing_fields and fcf > 0 and shares > 0 and growth_result is not None:
        auto_assumptions = build_auto_dcf_assumptions(
            metrics=m,
            growth_score=growth_result["score"],
            data_coverage_pct=data["data_coverage_pct"],
        )

        scenario_result = run_dcf_scenarios(
            base_free_cash_flow=fcf,
            net_debt=net_debt,
            shares_outstanding=shares,
            market_price=market_price,
            request_cls=DCFRequest,
            assumptions_bundle=auto_assumptions,
        )

        base_case = scenario_result["scenarios"]["base"]

        dcf_payload = {
            "result": base_case["result"],
            "scenarios": scenario_result,
            "missing_fields": [],
            "note": "銘柄別の実績成長率・利益率・Growth Score・データ取得率からDCF前提を自動生成しています。",
        }

        base_fair = base_case["fair_value_per_share"]
        if base_fair is not None and market_price not in (None, 0):
            mispricing_result = score_mispricing(
                MispricingRequest(
                    fair_value=base_fair,
                    market_price=market_price,
                    growth_score=growth_result["score"],
                    data_coverage_pct=data["data_coverage_pct"],
                )
            )
    else:
        if dcf_missing_fields:
            dcf_payload["note"] = "DCFに必要な実データが不足しているため未計算です。"
        elif fcf is not None and fcf <= 0:
            dcf_payload["note"] = "FCFが正でないためDCFを計算していません。"
            dcf_payload["missing_fields"] = []
        elif growth_result is None:
            dcf_payload["note"] = "DCF前提生成に必要なGrowth Scoreを算出できないため未計算です。"

    score_items = []
    score_weights = []

    if growth_result is not None:
        score_items.append(growth_result["score"])
        score_weights.append(0.35)
    if change_result is not None:
        score_items.append(change_result["score"])
        score_weights.append(0.20)
    if momentum_result is not None:
        score_items.append(momentum_result["score"])
        score_weights.append(0.25)
    if mispricing_result is not None:
        score_items.append(mispricing_result["score"])
        score_weights.append(0.20)

    if score_items:
        total_weight = sum(score_weights)
        ai_score = round(
            sum(score * weight for score, weight in zip(score_items, score_weights)) / total_weight,
            1,
        )
    else:
        ai_score = None

    if ai_score is None:
        rank = None
    elif ai_score >= 90:
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
        "comparison_period": data.get("comparison_period"),
        "data_status": data["data_status"],
        "data_source": data["data_source"],
        "data_retrieved_at_utc": data.get("data_retrieved_at_utc"),
        "data_coverage_pct": data["data_coverage_pct"],
        "missing_fields": data["missing_fields"],
        "market_price": market_price,
        "forecast_revision": data.get("forecast_revision"),
        "metrics": m,
        "growth": {"result": growth_result, "missing_fields": growth_missing},
        "change": {
            "result": change_result,
            "missing_fields": change_missing,
            "guidance_note": None if m.get("guidance_revision_available")
            else "会社予想修正率は取得できていないため、Change Score内部では中立値を使用。",
        },
        "momentum": momentum_result,
        "dcf": dcf_payload,
        "mispricing": mispricing_result,
        "ai_score": ai_score,
        "rank": rank,
        "score_weights": {
            "growth": 0.35,
            "change": 0.20,
            "momentum": 0.25,
            "mispricing": 0.20,
            "note": "取得できたスコアだけで重みを再正規化します。",
        },
        "quality_flags": {
            "guidance_revision_available": bool(m.get("guidance_revision_available", False)),
            "dcf_available": dcf_payload["scenarios"] is not None,
            "auto_dcf_assumptions": dcf_payload["scenarios"] is not None,
            "momentum_v2": True,
            "jquants_forecast_revision": bool(m.get("jquants_revision_available", False)),
        },
        "note": "外部データに欠損がある場合は架空値を生成せず、missing_fieldsに明示します。",
    }


@app.get("/analyze/{ticker}")
def analyze(ticker: str):
    return _analyze_one(ticker)


@app.post("/analyze/batch")
def analyze_batch(req: BatchAnalyzeRequest):
    sort_by = req.sort_by.lower().strip()
    allowed = {"momentum", "ai", "growth", "change", "revision", "mispricing"}
    if sort_by not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"sort_by は {sorted(allowed)} から指定してください。"
        )

    rows = []
    errors = []

    # 重複を除き入力順を維持
    seen = set()
    tickers = []
    for raw in req.tickers:
        ticker = str(raw).strip().upper().replace(".T", "")
        if ticker and ticker not in seen:
            seen.add(ticker)
            tickers.append(ticker)

    for ticker in tickers:
        try:
            result = _analyze_one(ticker)
            row = _summary_from_analysis(result)

            if req.only_upward_revision and row.get("is_upward_revision") is not True:
                continue
            if not _rank_at_least(row.get("rank"), req.min_rank):
                continue

            rows.append(row)
        except HTTPException as e:
            errors.append({
                "ticker": ticker,
                "status_code": e.status_code,
                "detail": e.detail,
            })
        except Exception as e:
            errors.append({
                "ticker": ticker,
                "status_code": 500,
                "detail": f"{type(e).__name__}: {str(e)}",
            })

    rows.sort(key=lambda x: _sort_value(x, sort_by), reverse=True)

    for i, row in enumerate(rows, start=1):
        row["position"] = i

    return {
        "version": "1.0.0",
        "sort_by": sort_by,
        "requested_count": len(tickers),
        "success_count": len(rows),
        "error_count": len(errors),
        "filters": {
            "min_rank": req.min_rank,
            "only_upward_revision": req.only_upward_revision,
        },
        "results": rows,
        "errors": errors,
        "note": "各銘柄を個別分析して要約・ランキング。取得できない値は架空補完しません。",
    }


@app.post("/ranking/momentum")
def ranking_momentum(req: BatchRankingRequest):
    batch = analyze_batch(
        BatchAnalyzeRequest(
            tickers=req.tickers,
            sort_by="momentum",
            min_rank=req.min_rank,
            only_upward_revision=req.only_upward_revision,
        )
    )
    batch["results"] = batch["results"][:req.limit]
    batch["ranking_type"] = "momentum"
    return batch


@app.post("/ranking/ai")
def ranking_ai(req: BatchRankingRequest):
    batch = analyze_batch(
        BatchAnalyzeRequest(
            tickers=req.tickers,
            sort_by="ai",
            min_rank=req.min_rank,
            only_upward_revision=req.only_upward_revision,
        )
    )
    batch["results"] = batch["results"][:req.limit]
    batch["ranking_type"] = "ai"
    return batch


@app.post("/ranking/upward-revision")
def ranking_upward_revision(req: BatchRankingRequest):
    batch = analyze_batch(
        BatchAnalyzeRequest(
            tickers=req.tickers,
            sort_by="revision",
            min_rank=req.min_rank,
            only_upward_revision=True,
        )
    )
    batch["results"] = batch["results"][:req.limit]
    batch["ranking_type"] = "upward_revision"
    return batch
