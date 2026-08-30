from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd
import yfinance as yf


def _first_existing(df: pd.DataFrame, names: list[str], col):
    if df is None or df.empty:
        return None
    for name in names:
        if name in df.index:
            value = df.loc[name, col]
            if pd.notna(value):
                try:
                    return float(value)
                except Exception:
                    return None
    return None


def _safe_pct_change(current, previous):
    if current is None or previous in (None, 0):
        return None
    return (current / previous - 1) * 100


def _safe_margin(profit, revenue):
    if profit is None or revenue in (None, 0):
        return None
    return profit / revenue * 100


def _safe_float(value):
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except Exception:
        return None


def _latest_price(t: yf.Ticker, info: dict[str, Any]):
    for key in ("currentPrice", "regularMarketPrice"):
        v = _safe_float(info.get(key))
        if v is not None and v > 0:
            return v
    try:
        hist = t.history(period="5d", auto_adjust=False)
        if not hist.empty:
            v = _safe_float(hist["Close"].dropna().iloc[-1])
            if v is not None and v > 0:
                return v
    except Exception:
        pass
    return None


def _company_name(info: dict[str, Any], symbol: str):
    return (
        info.get("longName")
        or info.get("shortName")
        or info.get("displayName")
        or symbol
    )


def get_company_snapshot(ticker: str):
    symbol = f"{ticker}.T"
    t = yf.Ticker(symbol)

    try:
        info = t.info or {}
    except Exception:
        info = {}

    try:
        financials = t.financials
    except Exception:
        financials = pd.DataFrame()

    try:
        cashflow = t.cashflow
    except Exception:
        cashflow = pd.DataFrame()

    if financials is None or financials.empty or len(financials.columns) < 2:
        return None

    cols = list(financials.columns)
    latest_col = cols[0]
    previous_col = cols[1]
    older_col = cols[2] if len(cols) >= 3 else None

    revenue_names = ["Total Revenue", "Operating Revenue"]
    op_profit_names = ["Operating Income", "Operating Profit"]
    net_income_names = ["Net Income", "Net Income Common Stockholders"]
    diluted_eps_names = ["Diluted EPS", "Basic EPS"]

    rev_latest = _first_existing(financials, revenue_names, latest_col)
    rev_prev = _first_existing(financials, revenue_names, previous_col)
    rev_older = _first_existing(financials, revenue_names, older_col) if older_col is not None else None

    op_latest = _first_existing(financials, op_profit_names, latest_col)
    op_prev = _first_existing(financials, op_profit_names, previous_col)
    op_older = _first_existing(financials, op_profit_names, older_col) if older_col is not None else None

    eps_latest = _first_existing(financials, diluted_eps_names, latest_col)
    eps_prev = _first_existing(financials, diluted_eps_names, previous_col)

    # EPS行が無い場合は純利益÷希薄化後株式数（取得できるときのみ）
    shares = _safe_float(info.get("sharesOutstanding")) or _safe_float(info.get("impliedSharesOutstanding"))
    if eps_latest is None or eps_prev is None:
        ni_latest = _first_existing(financials, net_income_names, latest_col)
        ni_prev = _first_existing(financials, net_income_names, previous_col)
        if shares and shares > 0:
            eps_latest = eps_latest if eps_latest is not None else (ni_latest / shares if ni_latest is not None else None)
            eps_prev = eps_prev if eps_prev is not None else (ni_prev / shares if ni_prev is not None else None)

    revenue_growth = _safe_pct_change(rev_latest, rev_prev)
    operating_profit_growth = _safe_pct_change(op_latest, op_prev)
    eps_growth = _safe_pct_change(eps_latest, eps_prev)

    latest_margin = _safe_margin(op_latest, rev_latest)
    previous_margin = _safe_margin(op_prev, rev_prev)
    margin_change = (
        latest_margin - previous_margin
        if latest_margin is not None and previous_margin is not None
        else None
    )

    previous_growth = _safe_pct_change(rev_prev, rev_older) if rev_older is not None else None
    latest_growth = revenue_growth

    # 黒字→赤字を「大幅改善」と誤判定しないためのフラグ
    sign_flip_penalty = bool(
        op_latest is not None and op_prev is not None and op_latest < 0 <= op_prev
    )

    market_price = _latest_price(t, info)

    # FCF: Yahoo FinanceのFree Cash Flow行を優先。なければ営業CF - 設備投資。
    base_fcf = None
    if cashflow is not None and not cashflow.empty and len(cashflow.columns) >= 1:
        cf_col = list(cashflow.columns)[0]
        base_fcf = _first_existing(cashflow, ["Free Cash Flow"], cf_col)
        if base_fcf is None:
            ocf = _first_existing(
                cashflow,
                ["Operating Cash Flow", "Total Cash From Operating Activities"],
                cf_col,
            )
            capex = _first_existing(cashflow, ["Capital Expenditure", "Capital Expenditures"], cf_col)
            if ocf is not None and capex is not None:
                # yfinanceの設備投資は負値で入ることが多い
                base_fcf = ocf + capex if capex < 0 else ocf - capex

    cash = _safe_float(info.get("totalCash"))
    debt = _safe_float(info.get("totalDebt"))
    net_debt = None
    if cash is not None or debt is not None:
        net_debt = (debt or 0.0) - (cash or 0.0)

    fields = {
        "revenue_growth_pct": revenue_growth,
        "operating_profit_growth_pct": operating_profit_growth,
        "eps_growth_pct": eps_growth,
        "operating_margin_pct": latest_margin,
        "latest_growth_pct": latest_growth,
        "previous_growth_pct": previous_growth,
        "margin_change_points": margin_change,
        "market_price": market_price,
        "free_cash_flow": base_fcf,
        "net_debt": net_debt,
        "shares_outstanding": shares,
    }

    missing_fields = [k for k, v in fields.items() if v is None]
    coverage = round((len(fields) - len(missing_fields)) / len(fields) * 100, 1)

    return {
        "ticker": ticker,
        "symbol": symbol,
        "company_name": _company_name(info, symbol),
        "fiscal_period": str(latest_col.date()) if hasattr(latest_col, "date") else str(latest_col),
        "comparison_period": {
            "latest": str(latest_col.date()) if hasattr(latest_col, "date") else str(latest_col),
            "previous": str(previous_col.date()) if hasattr(previous_col, "date") else str(previous_col),
            "older": str(older_col.date()) if older_col is not None and hasattr(older_col, "date") else (str(older_col) if older_col is not None else None),
        },
        "data_status": "external_yfinance",
        "data_source": "Yahoo Finance via yfinance",
        "data_retrieved_at_utc": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "data_coverage_pct": coverage,
        "missing_fields": missing_fields,
        "market_price": market_price,
        "metrics": {
            "revenue_growth_pct": revenue_growth,
            "operating_profit_growth_pct": operating_profit_growth,
            "eps_growth_pct": eps_growth,
            "operating_margin_pct": latest_margin,
            "latest_growth_pct": latest_growth,
            "previous_growth_pct": previous_growth,
            "margin_change_points": margin_change,
            "guidance_revision_pct": None,
            "guidance_revision_available": False,
            "sign_flip_penalty": sign_flip_penalty,
            "free_cash_flow": base_fcf,
            "net_debt": net_debt,
            "shares_outstanding": shares,
        },
    }
