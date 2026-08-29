def _clamp(value, low=0, high=100):
    return max(low, min(high, value))

def score_growth(req):
    revenue = _clamp(req.revenue_growth_pct / 30 * 100)
    op_profit = _clamp(req.operating_profit_growth_pct / 50 * 100)
    eps = _clamp(req.eps_growth_pct / 40 * 100)
    margin = _clamp(req.operating_margin_pct / 20 * 100)

    score = round((revenue + op_profit + eps + margin) / 4, 1)

    return {
        "score": score,
        "components": {
            "revenue_growth": round(revenue, 1),
            "operating_profit_growth": round(op_profit, 1),
            "eps_growth": round(eps, 1),
            "operating_margin": round(margin, 1),
        },
    }

def score_change(req):
    acceleration = req.latest_growth_pct - req.previous_growth_pct

    acceleration_score = _clamp(50 + acceleration * 2.0)
    margin_score = _clamp(50 + req.margin_change_points * 10.0)
    guidance_score = _clamp(50 + req.guidance_revision_pct * 2.0)

    score = round(
        acceleration_score * 0.50
        + margin_score * 0.30
        + guidance_score * 0.20,
        1,
    )

    label = (
        "非常に強い" if score >= 85 else
        "強い" if score >= 70 else
        "中立" if score >= 50 else
        "悪化"
    )

    return {
        "score": score,
        "components": {
            "growth_acceleration": round(acceleration_score, 1),
            "margin_change": round(margin_score, 1),
            "guidance_revision": round(guidance_score, 1),
        },
        "label": label,
    }

def score_mispricing(req):
    upside_pct = (req.fair_value / req.market_price - 1) * 100
    valuation_score = _clamp(50 + upside_pct)

    score = round(
        valuation_score * 0.60
        + req.growth_score * 0.30
        + req.data_coverage_pct * 0.10,
        1,
    )

    label = (
        "大幅割安" if upside_pct >= 30 else
        "割安" if upside_pct >= 10 else
        "適正圏" if upside_pct > -10 else
        "割高"
    )

    return {
        "score": score,
        "upside_pct": round(upside_pct, 1),
        "label": label,
    }

def run_dcf(req):
    r = req.discount_rate_pct / 100
    g = req.growth_rate_pct / 100
    tg = req.terminal_growth_rate_pct / 100

    if r <= tg:
        return {"error": "割引率は永久成長率より高くしてください。"}

    fcf = req.base_free_cash_flow
    projected_fcfs = []
    pv_fcfs = 0.0

    for year in range(1, req.years + 1):
        fcf *= (1 + g)
        projected_fcfs.append(round(fcf, 4))
        pv_fcfs += fcf / ((1 + r) ** year)

    terminal_value = fcf * (1 + tg) / (r - tg)
    pv_terminal = terminal_value / ((1 + r) ** req.years)

    enterprise_value = pv_fcfs + pv_terminal
    equity_value = enterprise_value - req.net_debt

    fair_value_per_share = None
    if req.shares_outstanding:
        fair_value_per_share = equity_value / req.shares_outstanding

    return {
        "enterprise_value": round(enterprise_value, 4),
        "equity_value": round(equity_value, 4),
        "fair_value_per_share": round(fair_value_per_share, 4) if fair_value_per_share is not None else None,
        "projected_fcfs": projected_fcfs,
        "terminal_value": round(terminal_value, 4),
    }
