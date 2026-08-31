def clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))

def score_growth(revenue_growth_pct, operating_profit_growth_pct, eps_growth_pct, operating_margin_pct):
    components = {
        "revenue_growth": clamp(revenue_growth_pct * 2.5),
        "operating_profit_growth": clamp(operating_profit_growth_pct * 2.0),
        "eps_growth": clamp(eps_growth_pct * 1.5),
        "operating_margin": clamp(operating_margin_pct * 4.0),
    }
    score = round(sum(components.values()) / len(components), 1)
    return {"score": score, "components": components}

def score_change(latest_growth_pct, previous_growth_pct, margin_change_points, guidance_revision_pct=0):
    acceleration = latest_growth_pct - previous_growth_pct
    components = {
        "growth_acceleration": clamp(50 + acceleration * 2),
        "margin_change": clamp(50 + margin_change_points * 5),
        "guidance_revision": clamp(50 + guidance_revision_pct * 5),
    }
    score = round(sum(components.values()) / len(components), 1)
    label = "非常に強い" if score >= 85 else "強い" if score >= 70 else "中立" if score >= 45 else "弱い"
    return {"score": score, "components": components, "label": label}

def score_mispricing(fair_value, market_price, growth_score, data_coverage_pct=100):
    upside_pct = (fair_value / market_price - 1) * 100
    valuation = clamp(50 + upside_pct)
    score = round((valuation * 0.7 + growth_score * 0.3) * (data_coverage_pct / 100), 1)
    label = "割安" if upside_pct >= 20 else "やや割安" if upside_pct >= 5 else "適正圏" if upside_pct > -10 else "割高"
    return {
        "score": score,
        "upside_pct": round(upside_pct, 1),
        "valuation_component": round(valuation, 1),
        "label": label,
    }

def calc_dcf(base_free_cash_flow, growth_rate_pct, discount_rate_pct,
             terminal_growth_rate_pct, years=5, net_debt=0, shares_outstanding=None):
    g = growth_rate_pct / 100
    r = discount_rate_pct / 100
    tg = terminal_growth_rate_pct / 100
    if r <= tg:
        raise ValueError("discount_rate_pct must be greater than terminal_growth_rate_pct")
    projected = []
    fcf = base_free_cash_flow
    pv_sum = 0.0
    for year in range(1, years + 1):
        fcf *= (1 + g)
        projected.append(fcf)
        pv_sum += fcf / ((1 + r) ** year)
    terminal_value = fcf * (1 + tg) / (r - tg)
    pv_terminal = terminal_value / ((1 + r) ** years)
    enterprise_value = pv_sum + pv_terminal
    equity_value = enterprise_value - net_debt
    fair = equity_value / shares_outstanding if shares_outstanding else None
    return {
        "enterprise_value": enterprise_value,
        "equity_value": equity_value,
        "fair_value_per_share": fair,
        "projected_fcfs": projected,
        "terminal_value": terminal_value,
    }

DCF_SCENARIOS = {
    "bear": {"label": "弱気", "growth_rate_pct": 5.0, "discount_rate_pct": 9.0, "terminal_growth_rate_pct": 0.5},
    "base": {"label": "標準", "growth_rate_pct": 10.0, "discount_rate_pct": 8.0, "terminal_growth_rate_pct": 1.0},
    "bull": {"label": "強気", "growth_rate_pct": 15.0, "discount_rate_pct": 7.0, "terminal_growth_rate_pct": 1.5},
}

def calc_dcf_scenarios(base_free_cash_flow, net_debt, shares_outstanding, market_price=None, years=5):
    scenarios = {}
    for key, p in DCF_SCENARIOS.items():
        result = calc_dcf(
            base_free_cash_flow=base_free_cash_flow,
            growth_rate_pct=p["growth_rate_pct"],
            discount_rate_pct=p["discount_rate_pct"],
            terminal_growth_rate_pct=p["terminal_growth_rate_pct"],
            years=years,
            net_debt=net_debt,
            shares_outstanding=shares_outstanding,
        )
        fair = result["fair_value_per_share"]
        scenarios[key] = {
            "label": p["label"],
            "assumptions": p,
            "fair_value_per_share": round(fair, 2) if fair is not None else None,
            "upside_pct": round((fair / market_price - 1) * 100, 1) if fair and market_price else None,
        }
    vals = [v["fair_value_per_share"] for v in scenarios.values() if v["fair_value_per_share"] is not None]
    return {
        "scenarios": scenarios,
        "fair_value_range": {"low": min(vals), "high": max(vals)} if vals else None,
        "market_price": market_price,
        "valuation_label": (
            "割安" if market_price and vals and market_price < min(vals)
            else "適正圏" if market_price and vals and min(vals) <= market_price <= max(vals)
            else "割高" if market_price and vals else None
        ),
    }
