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



def score_momentum(metrics, growth_result, change_result):
    """
    決算Momentum v2
    財務モメンタム + 直近株価反応 + 出来高 + TOPIX連動ETF対比RS。
    会社予想修正はJ-Quants接続時に実データを使用し、未取得時は中立50点。TDnet原文書類は未接続。
    """

    def neutral_if_none(v, fn):
        return 50.0 if v is None else _clamp(fn(v))

    revenue_growth = metrics.get("revenue_growth_pct")
    op_growth = metrics.get("operating_profit_growth_pct")
    eps_growth = metrics.get("eps_growth_pct")
    margin_change = metrics.get("margin_change_points")
    latest_growth = metrics.get("latest_growth_pct")
    previous_growth = metrics.get("previous_growth_pct")
    guidance = metrics.get("guidance_revision_pct")

    # 財務モメンタム
    revenue_score = neutral_if_none(revenue_growth, lambda x: 50 + x * 1.2)
    op_profit_score = neutral_if_none(op_growth, lambda x: 50 + x * 0.8)
    eps_score = neutral_if_none(eps_growth, lambda x: 50 + x * 0.8)
    margin_score = neutral_if_none(margin_change, lambda x: 50 + x * 6.0)

    if latest_growth is None or previous_growth is None:
        acceleration_score = 50.0
        acceleration = None
    else:
        acceleration = latest_growth - previous_growth
        acceleration_score = _clamp(50 + acceleration * 1.5)

    if guidance is None:
        guidance_score = 50.0
        guidance_available = False
    else:
        guidance_score = _clamp(50 + guidance * 2.0)
        guidance_available = True

    # 市場反応
    ret5 = metrics.get("price_return_5d_pct")
    ret20 = metrics.get("price_return_20d_pct")
    volume_ratio = metrics.get("volume_ratio_20d")
    rs63 = metrics.get("rs_63d_pct")
    rs126 = metrics.get("rs_126d_pct")

    price5_score = neutral_if_none(ret5, lambda x: 50 + x * 3.0)
    price20_score = neutral_if_none(ret20, lambda x: 50 + x * 1.5)
    price_reaction_score = round(price5_score * 0.60 + price20_score * 0.40, 1)

    volume_score = neutral_if_none(volume_ratio, lambda x: 25 + x * 25)

    rs63_score = neutral_if_none(rs63, lambda x: 50 + x * 1.5)
    rs126_score = neutral_if_none(rs126, lambda x: 50 + x * 1.0)
    rs_score = round(rs63_score * 0.60 + rs126_score * 0.40, 1)

    # v2: 財務60% + 市場40%
    score = round(
        revenue_score * 0.10
        + op_profit_score * 0.15
        + eps_score * 0.10
        + margin_score * 0.08
        + acceleration_score * 0.10
        + guidance_score * 0.07
        + price_reaction_score * 0.15
        + volume_score * 0.10
        + rs_score * 0.15,
        1,
    )

    rank = (
        "S" if score >= 90 else
        "A" if score >= 80 else
        "B" if score >= 65 else
        "C" if score >= 50 else
        "D"
    )

    label = (
        "非常に強い決算モメンタム" if score >= 90 else
        "強い決算モメンタム" if score >= 80 else
        "良好" if score >= 65 else
        "中立" if score >= 50 else
        "弱い"
    )

    growth_score = growth_result["score"] if growth_result else None
    change_score = change_result["score"] if change_result else None

    return {
        "version": "v2",
        "score": score,
        "rank": rank,
        "label": label,
        "components": {
            "revenue_growth": round(revenue_score, 1),
            "operating_profit_growth": round(op_profit_score, 1),
            "eps_growth": round(eps_score, 1),
            "margin_change": round(margin_score, 1),
            "growth_acceleration": round(acceleration_score, 1),
            "guidance_revision": round(guidance_score, 1),
            "price_reaction": round(price_reaction_score, 1),
            "volume": round(volume_score, 1),
            "relative_strength": round(rs_score, 1),
            "growth_score_support": growth_score,
            "change_score_support": change_score,
        },
        "market": {
            "price_return_5d_pct": round(ret5, 2) if ret5 is not None else None,
            "price_return_20d_pct": round(ret20, 2) if ret20 is not None else None,
            "volume_ratio_20d": round(volume_ratio, 2) if volume_ratio is not None else None,
            "rs_63d_pct": round(rs63, 2) if rs63 is not None else None,
            "rs_126d_pct": round(rs126, 2) if rs126 is not None else None,
            "benchmark": "1306.T",
        },
        "raw": {
            "growth_acceleration_pct_points": round(acceleration, 2) if acceleration is not None else None,
            "guidance_revision_available": guidance_available,
            "tdnet_revision_available": bool(metrics.get("tdnet_revision_available", False)),
        },
        "weights": {
            "financial": 0.60,
            "market": 0.40,
        },
        "note": "v2は財務モメンタムに直近株価反応・出来高・TOPIX連動ETF対比RSを追加。J-Quants会社予想修正を利用可能。未取得時は中立扱い。TDnet原文書類は未接続。",
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


def _safe_growth_component(value, floor=-20.0, cap=60.0):
    if value is None:
        return None
    return max(floor, min(cap, float(value)))


def build_auto_dcf_assumptions(metrics, growth_score, data_coverage_pct):
    """
    銘柄ごとの実績成長率とGrowth Scoreから、DCF前提を自動生成。
    WACCを厳密推計するものではなく、比較用の保守的なルールベース。
    """
    revenue = _safe_growth_component(metrics.get("revenue_growth_pct"))
    op_profit = _safe_growth_component(metrics.get("operating_profit_growth_pct"))
    eps = _safe_growth_component(metrics.get("eps_growth_pct"))
    margin = metrics.get("operating_margin_pct")

    available = [x for x in [revenue, op_profit, eps] if x is not None]

    if available:
        # 極端値の影響を抑えつつ、利益成長をやや重視
        weighted_parts = []
        weights = []
        if revenue is not None:
            weighted_parts.append(revenue * 0.35)
            weights.append(0.35)
        if op_profit is not None:
            weighted_parts.append(op_profit * 0.35)
            weights.append(0.35)
        if eps is not None:
            weighted_parts.append(eps * 0.30)
            weights.append(0.30)
        raw_growth = sum(weighted_parts) / sum(weights)
    else:
        raw_growth = 5.0

    # Growth Scoreを「継続可能性」の補正として使う
    score_adjustment = (growth_score - 50.0) / 10.0  # 50点=0、100点=+5
    base_growth = raw_growth * 0.35 + score_adjustment

    # FCF成長率としてはかなり保守的に制限
    base_growth = max(0.0, min(18.0, base_growth))

    # 高利益率企業は上限方向、低利益率は少し抑制
    if margin is not None:
        if margin >= 25:
            base_growth = min(20.0, base_growth + 1.0)
        elif margin < 5:
            base_growth = max(0.0, base_growth - 1.5)

    # データ取得率が低いほど割引率を高める
    coverage_penalty = max(0.0, (100.0 - data_coverage_pct) / 20.0)
    base_discount = 8.0 + coverage_penalty
    base_discount = max(7.5, min(10.5, base_discount))

    bear_growth = max(-2.0, base_growth - 5.0)
    bull_growth = min(25.0, base_growth + 5.0)

    return {
        "method": "rule_based_auto_v1",
        "inputs": {
            "revenue_growth_pct": revenue,
            "operating_profit_growth_pct": op_profit,
            "eps_growth_pct": eps,
            "operating_margin_pct": margin,
            "growth_score": growth_score,
            "data_coverage_pct": data_coverage_pct,
        },
        "scenarios": {
            "bear": {
                "label": "弱気",
                "growth_rate_pct": round(bear_growth, 2),
                "discount_rate_pct": round(base_discount + 1.0, 2),
                "terminal_growth_rate_pct": 0.5,
            },
            "base": {
                "label": "標準",
                "growth_rate_pct": round(base_growth, 2),
                "discount_rate_pct": round(base_discount, 2),
                "terminal_growth_rate_pct": 1.0,
            },
            "bull": {
                "label": "強気",
                "growth_rate_pct": round(bull_growth, 2),
                "discount_rate_pct": round(max(6.5, base_discount - 1.0), 2),
                "terminal_growth_rate_pct": 1.5,
            },
        },
        "note": "実績成長率・利益率・Growth Score・データ取得率を使った比較用の自動前提。WACCの厳密推計ではありません。",
    }


def run_dcf_scenarios(base_free_cash_flow, net_debt, shares_outstanding, market_price, request_cls, assumptions_bundle):
    scenarios = {}

    for key, assumptions in assumptions_bundle["scenarios"].items():
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

    low = min(fair_values) if fair_values else None
    high = max(fair_values) if fair_values else None

    if fair_values and market_price is not None:
        valuation_label = (
            "割安" if market_price < low
            else "適正圏" if market_price <= high
            else "割高"
        )
    else:
        valuation_label = None

    return {
        "assumption_engine": assumptions_bundle,
        "scenarios": scenarios,
        "fair_value_range": {"low": low, "high": high} if fair_values else None,
        "market_price": market_price,
        "valuation_label": valuation_label,
    }
