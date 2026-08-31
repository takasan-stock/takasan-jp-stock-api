import yfinance as yf
import pandas as pd

def _num(v):
    try:
        if v is None or pd.isna(v):
            return None
        return float(v)
    except Exception:
        return None

def _row(df, names, col=0):
    if df is None or df.empty:
        return None
    for name in names:
        if name in df.index:
            try:
                return _num(df.loc[name].iloc[col])
            except Exception:
                pass
    return None

def _growth(cur, prev):
    if cur is None or prev in (None, 0):
        return 0.0
    return (cur / abs(prev) - 1) * 100

def get_company_snapshot(ticker: str):
    symbol = ticker if ticker.endswith(".T") else f"{ticker}.T"
    t = yf.Ticker(symbol)
    try:
        info = t.info or {}
        fin = t.financials
        qfin = t.quarterly_financials
        cf = t.cashflow
        qcf = t.quarterly_cashflow
        bs = t.balance_sheet
        qbs = t.quarterly_balance_sheet
        hist = t.history(period="5d")
    except Exception:
        return None

    market_price = _num(info.get("currentPrice"))
    if market_price is None and hist is not None and not hist.empty:
        market_price = _num(hist["Close"].iloc[-1])

    # Latest/previous/older annual revenue and operating income
    rev0 = _row(fin, ["Total Revenue"], 0); rev1 = _row(fin, ["Total Revenue"], 1); rev2 = _row(fin, ["Total Revenue"], 2)
    op0 = _row(fin, ["Operating Income"], 0); op1 = _row(fin, ["Operating Income"], 1)
    ni0 = _row(fin, ["Net Income"], 0); ni1 = _row(fin, ["Net Income"], 1)

    revenue_growth = _growth(rev0, rev1)
    op_growth = _growth(op0, op1)
    eps_growth = _growth(ni0, ni1)
    previous_growth = _growth(rev1, rev2)
    operating_margin = (op0 / rev0 * 100) if op0 is not None and rev0 else 0.0
    prev_margin = (op1 / rev1 * 100) if op1 is not None and rev1 else 0.0

    # FCF: info -> quarterly -> annual
    fcf = _num(info.get("freeCashflow"))
    if fcf is None:
        fcf = _row(qcf, ["Free Cash Flow"], 0)
    if fcf is None:
        fcf = _row(cf, ["Free Cash Flow"], 0)

    # shares: info -> fast_info -> BS
    shares = _num(info.get("sharesOutstanding"))
    if shares is None:
        try: shares = _num(t.fast_info.get("shares"))
        except Exception: pass
    if shares is None:
        shares = _row(qbs, ["Ordinary Shares Number", "Share Issued"], 0)
    if shares is None:
        shares = _row(bs, ["Ordinary Shares Number", "Share Issued"], 0)

    # net debt: info -> statements -> total debt - cash
    net_debt = _num(info.get("netDebt"))
    if net_debt is None:
        net_debt = _row(qbs, ["Net Debt"], 0)
    if net_debt is None:
        net_debt = _row(bs, ["Net Debt"], 0)
    if net_debt is None:
        debt = _num(info.get("totalDebt"))
        cash = _num(info.get("totalCash"))
        if debt is not None and cash is not None:
            net_debt = debt - cash

    missing = [k for k,v in {
        "free_cash_flow": fcf, "net_debt": net_debt, "shares_outstanding": shares
    }.items() if v is None]
    coverage = round((3-len(missing))/3*100, 1)

    name = info.get("longName") or info.get("shortName") or f"{ticker}.T"
    latest_date = str(fin.columns[0].date()) if fin is not None and not fin.empty else None
    prev_date = str(fin.columns[1].date()) if fin is not None and len(fin.columns)>1 else None
    older_date = str(fin.columns[2].date()) if fin is not None and len(fin.columns)>2 else None

    return {
        "ticker": ticker.replace(".T",""),
        "company_name": name,
        "fiscal_period": latest_date,
        "comparison_period": {"latest": latest_date, "previous": prev_date, "older": older_date},
        "data_status": "external_yfinance",
        "data_source": "Yahoo Finance via yfinance",
        "data_coverage_pct": coverage,
        "missing_fields": missing,
        "market_price": market_price,
        "metrics": {
            "revenue_growth_pct": revenue_growth,
            "operating_profit_growth_pct": op_growth,
            "eps_growth_pct": eps_growth,
            "operating_margin_pct": operating_margin,
            "latest_growth_pct": revenue_growth,
            "previous_growth_pct": previous_growth,
            "margin_change_points": operating_margin-prev_margin,
            "guidance_revision_pct": None,
            "guidance_revision_available": False,
            "free_cash_flow": fcf,
            "net_debt": net_debt,
            "shares_outstanding": shares,
        }
    }
