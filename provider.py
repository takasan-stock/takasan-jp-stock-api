from __future__ import annotations
from datetime import datetime
from io import BytesIO
from typing import Any
import re
import pandas as pd
import requests
import yfinance as yf

JPX_MASTER_URLS = [
    "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls",
    "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xlsx",
]
COMPANY_NAME_FALLBACK = {"9563": "ATLAS TECHNOLOGIES", "6857": "アドバンテスト"}
_JPX_NAME_CACHE = None

def _safe_float(v):
    try:
        if v is None or pd.isna(v): return None
        return float(v)
    except Exception:
        return None

def _first_existing(df, names, col):
    if df is None or df.empty or col is None: return None
    for name in names:
        if name in df.index:
            return _safe_float(df.loc[name, col])
    return None

def _first_existing_anycol(df, names):
    if df is None or df.empty: return None
    for col in list(df.columns):
        v = _first_existing(df, names, col)
        if v is not None: return v
    return None

def _safe_pct_change(cur, prev):
    if cur is None or prev in (None, 0): return None
    return (cur / prev - 1) * 100

def _safe_margin(profit, revenue):
    if profit is None or revenue in (None, 0): return None
    return profit / revenue * 100

def _normalize_code(value):
    if value is None or pd.isna(value): return None
    text = re.sub(r"\.0$", "", str(value).strip())
    m = re.search(r"(\d{4})", text)
    return m.group(1) if m else None

def _load_jpx_name_map():
    global _JPX_NAME_CACHE
    if _JPX_NAME_CACHE is not None: return _JPX_NAME_CACHE
    result = {}
    for url in JPX_MASTER_URLS:
        try:
            r = requests.get(url, timeout=12, headers={"User-Agent":"Mozilla/5.0"})
            r.raise_for_status()
            df = pd.read_excel(BytesIO(r.content))
            code_col = next((c for c in df.columns if "コード" in str(c)), None)
            name_col = next((c for c in df.columns if "銘柄名" in str(c) or "会社名" in str(c)), None)
            if code_col is None or name_col is None: continue
            for _, row in df[[code_col, name_col]].dropna().iterrows():
                code = _normalize_code(row[code_col])
                if code: result[code] = str(row[name_col]).strip()
            if result: break
        except Exception:
            pass
    for k,v in COMPANY_NAME_FALLBACK.items():
        result.setdefault(k,v)
    _JPX_NAME_CACHE = result
    return result

def _company_name(info, ticker, symbol):
    name = info.get("longName") or info.get("shortName") or info.get("displayName")
    if name and str(name).strip() not in {ticker, symbol}: return str(name).strip()
    return _load_jpx_name_map().get(ticker) or symbol

def _latest_price(t, info):
    for key in ("currentPrice","regularMarketPrice"):
        v = _safe_float(info.get(key))
        if v and v > 0: return v
    try:
        fi = t.fast_info
        for attr in ("last_price","lastPrice"):
            v = _safe_float(getattr(fi, attr, None))
            if v and v > 0: return v
    except Exception: pass
    try:
        hist = t.history(period="5d", auto_adjust=False)
        if not hist.empty: return _safe_float(hist["Close"].dropna().iloc[-1])
    except Exception: pass
    return None

def _shares_outstanding(t, info, annual_bs, quarterly_bs):
    candidates = [
        _safe_float(info.get("sharesOutstanding")),
        _safe_float(info.get("impliedSharesOutstanding")),
    ]
    try:
        fi = t.fast_info
        candidates += [_safe_float(getattr(fi,"shares",None)), _safe_float(getattr(fi,"shares_outstanding",None))]
    except Exception: pass
    names = ["Ordinary Shares Number","Share Issued","Common Stock Shares Outstanding"]
    candidates += [_first_existing_anycol(quarterly_bs,names), _first_existing_anycol(annual_bs,names)]
    for v in candidates:
        if v is not None and v > 0: return v
    return None

def _net_debt(info, annual_bs, quarterly_bs):
    debt = _safe_float(info.get("totalDebt"))
    cash = _safe_float(info.get("totalCash"))
    if debt is not None or cash is not None:
        return (debt or 0.0) - (cash or 0.0)
    for bs in (quarterly_bs, annual_bs):
        v = _first_existing_anycol(bs, ["Net Debt"])
        if v is not None: return v
    debt_names = ["Total Debt","Long Term Debt And Capital Lease Obligation","Long Term Debt"]
    cash_names = ["Cash Cash Equivalents And Short Term Investments","Cash And Cash Equivalents","Cash Financial"]
    for bs in (quarterly_bs, annual_bs):
        d = _first_existing_anycol(bs, debt_names)
        c = _first_existing_anycol(bs, cash_names)
        if d is not None or c is not None:
            return (d or 0.0) - (c or 0.0)
    return None

def _free_cash_flow(annual_cf, quarterly_cf):
    for cf in (quarterly_cf, annual_cf):
        if cf is None or cf.empty: continue
        fcf = _first_existing_anycol(cf, ["Free Cash Flow"])
        if fcf is not None: return fcf
        ocf = _first_existing_anycol(cf, ["Operating Cash Flow","Total Cash From Operating Activities"])
        capex = _first_existing_anycol(cf, ["Capital Expenditure","Capital Expenditures"])
        if ocf is not None and capex is not None:
            return ocf + capex if capex < 0 else ocf - capex
    return None

def get_company_snapshot(ticker: str):
    symbol = f"{ticker}.T"
    t = yf.Ticker(symbol)
    try: info = t.info or {}
    except Exception: info = {}

    def _df(attr):
        try:
            x = getattr(t, attr)
            return x if x is not None else pd.DataFrame()
        except Exception:
            return pd.DataFrame()

    financials = _df("financials")
    annual_cf = _df("cashflow")
    quarterly_cf = _df("quarterly_cashflow")
    annual_bs = _df("balance_sheet")
    quarterly_bs = _df("quarterly_balance_sheet")

    if financials.empty or len(financials.columns) < 2: return None
    cols = list(financials.columns)
    latest, previous = cols[0], cols[1]
    older = cols[2] if len(cols) >= 3 else None

    rev_names = ["Total Revenue","Operating Revenue"]
    op_names = ["Operating Income","Operating Profit"]
    ni_names = ["Net Income","Net Income Common Stockholders"]
    eps_names = ["Diluted EPS","Basic EPS"]

    rev_l = _first_existing(financials, rev_names, latest)
    rev_p = _first_existing(financials, rev_names, previous)
    rev_o = _first_existing(financials, rev_names, older) if older is not None else None
    op_l = _first_existing(financials, op_names, latest)
    op_p = _first_existing(financials, op_names, previous)

    shares = _shares_outstanding(t, info, annual_bs, quarterly_bs)
    eps_l = _first_existing(financials, eps_names, latest)
    eps_p = _first_existing(financials, eps_names, previous)
    if (eps_l is None or eps_p is None) and shares:
        ni_l = _first_existing(financials, ni_names, latest)
        ni_p = _first_existing(financials, ni_names, previous)
        if eps_l is None and ni_l is not None: eps_l = ni_l / shares
        if eps_p is None and ni_p is not None: eps_p = ni_p / shares

    revenue_growth = _safe_pct_change(rev_l, rev_p)
    operating_profit_growth = _safe_pct_change(op_l, op_p)
    eps_growth = _safe_pct_change(eps_l, eps_p)
    latest_margin = _safe_margin(op_l, rev_l)
    previous_margin = _safe_margin(op_p, rev_p)
    margin_change = latest_margin - previous_margin if latest_margin is not None and previous_margin is not None else None
    previous_growth = _safe_pct_change(rev_p, rev_o) if rev_o is not None else None

    market_price = _latest_price(t, info)
    base_fcf = _free_cash_flow(annual_cf, quarterly_cf)
    net_debt = _net_debt(info, annual_bs, quarterly_bs)

    fields = {
        "revenue_growth_pct": revenue_growth,
        "operating_profit_growth_pct": operating_profit_growth,
        "eps_growth_pct": eps_growth,
        "operating_margin_pct": latest_margin,
        "latest_growth_pct": revenue_growth,
        "previous_growth_pct": previous_growth,
        "margin_change_points": margin_change,
        "market_price": market_price,
        "free_cash_flow": base_fcf,
        "net_debt": net_debt,
        "shares_outstanding": shares,
    }
    missing = [k for k,v in fields.items() if v is None]
    coverage = round((len(fields)-len(missing))/len(fields)*100,1)

    return {
        "ticker": ticker,
        "symbol": symbol,
        "company_name": _company_name(info,ticker,symbol),
        "fiscal_period": str(latest.date()) if hasattr(latest,"date") else str(latest),
        "comparison_period": {
            "latest": str(latest.date()) if hasattr(latest,"date") else str(latest),
            "previous": str(previous.date()) if hasattr(previous,"date") else str(previous),
            "older": str(older.date()) if older is not None and hasattr(older,"date") else (str(older) if older is not None else None)
        },
        "data_status":"external_yfinance+jpx_name_master",
        "data_source":"Yahoo Finance via yfinance + JPX name master",
        "data_retrieved_at_utc":datetime.utcnow().isoformat(timespec="seconds")+"Z",
        "data_coverage_pct":coverage,
        "missing_fields":missing,
        "market_price":market_price,
        "metrics":{
            "revenue_growth_pct":revenue_growth,
            "operating_profit_growth_pct":operating_profit_growth,
            "eps_growth_pct":eps_growth,
            "operating_margin_pct":latest_margin,
            "latest_growth_pct":revenue_growth,
            "previous_growth_pct":previous_growth,
            "margin_change_points":margin_change,
            "guidance_revision_pct":None,
            "guidance_revision_available":False,
            "sign_flip_penalty":bool(op_l is not None and op_p is not None and op_l < 0 <= op_p),
            "free_cash_flow":base_fcf,
            "net_debt":net_debt,
            "shares_outstanding":shares,
        }
    }
