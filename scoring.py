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

    acceleration_score = _clamp(50 + acceleration * 1.25)
    margin_score = _clamp(50 + req.margin_change_points * 6.0)

    if req.guidance_revision_pct is None:
        guidance_score = 50.0
        guidance_available = False
    else:
        guidance_score = _clamp(50 + req.guidance_revision_pct * 2.0)
        guidance_available = True

    if getattr(req, "sign_flip_penalty", False):
        acceleration_score = min(acceleration_score, 35.0)
        margin_score = min(margin_score, 35.0)

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
        "guidance_available": guidance_available,
        "sign_flip_penalty": getattr(req, "sign_flip_penalty", False),
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


DCF_SCENARIOS = {
    "bear": {
        "label": "弱気",
        "growth_rate_pct": 5.0,
        "discount_rate_pct": 9.0,
        "terminal_growth_rate_pct": 0.5,
    },
    "base": {
        "label": "標準",
        "growth_rate_pct": 10.0,
        "discount_rate_pct": 8.0,
        "terminal_growth_rate_pct": 1.0,
    },
    "bull": {
        "label": "強気",
        "growth_rate_pct": 15.0,
        "discount_rate_pct": 7.0,
        "terminal_growth_rate_pct": 1.5,
    },
}


def run_dcf_scenarios(base_free_cash_flow, net_debt, shares_outstanding, market_price, request_cls):
    scenarios = {}

    for key, assumptions in DCF_SCENARIOS.items():
        req = request_cls(
            base_free_cash_flow=base_free_cash_flow,
            growth_rate_pct=assumptions["growth_rate_pct"],
            discount_rate_pct=assumptions["discount_rate_pct"],
            terminal_growth_rate_pct=assumptions["terminal_growth_rate_pct"],
            years=5,
            net_debt=net_debt,
            shares_outstanding=shares_outstanding,
        )
        result = run_dcf(req)
        fair = result.get("fair_value_per_share")

        scenarios[key] = {
            "label": assumptions["label"],
            "assumptions": assumptions,
            "result": result,
            "fair_value_per_share": fair,
            "upside_pct": (
                round((fair / market_price - 1) * 100, 1)
                if fair is not None and market_price not in (None, 0)
                else None
            ),
        }

    fair_values = [
        x["fair_value_per_share"]
        for x in scenarios.values()
        if x["fair_value_per_share"] is not None
    ]

    if fair_values and market_price is not None:
        low = min(fair_values)
        high = max(fair_values)
        valuation_label = (
            "割安" if market_price < low
            else "適正圏" if market_price <= high
            else "割高"
        )
    else:
        low = high = None
        valuation_label = None

    return {
        "scenarios": scenarios,
        "fair_value_range": {"low": low, "high": high} if fair_values else None,
        "market_price": market_price,
        "valuation_label": valuation_label,
    }
