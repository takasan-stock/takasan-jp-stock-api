from __future__ import annotations

from typing import Any

import pandas as pd
import yfinance as yf


def _num(value: Any) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _row(df: pd.DataFrame, names: list[str]) -> pd.Series | None:
    if df is None or df.empty:
        return None

    for name in names:
        if name in df.index:
            value = df.loc[name]
            if isinstance(value, pd.DataFrame):
                value = value.iloc[0]
            if isinstance(value, pd.Series):
                return value
    return None


def _ordered_values(series: pd.Series | None) -> list[float | None]:
    if series is None or series.empty:
        return []

    try:
        ordered = series.reindex(sorted(series.index, reverse=True))
    except Exception:
        ordered = series

    return [_num(v) for v in ordered.tolist()]


def _growth(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return (current / previous - 1.0) * 100.0


def _latest_price(stock: yf.Ticker) -> float | None:
    try:
        price = _num(stock.fast_info.get("last_price"))
        if price is not None and price > 0:
            return price
    except Exception:
        pass

    try:
        hist = stock.history(period="5d", auto_adjust=False)
        if not hist.empty:
            price = _num(hist["Close"].dropna().iloc[-1])
            if price is not None and price > 0:
                return price
    except Exception:
        pass

    return None


def _free_cash_flow(cashflow: pd.DataFrame) -> float | None:
    direct = _ordered_values(_row(cashflow, ["Free Cash Flow"]))
    if direct and direct[0] is not None:
        return direct[0]

    cfo = _ordered_values(
        _row(
            cashflow,
            [
                "Operating Cash Flow",
                "Total Cash From Operating Activities",
            ],
        )
    )
    capex = _ordered_values(_row(cashflow, ["Capital Expenditure", "Capital Expenditures"]))

    if cfo and capex and cfo[0] is not None and capex[0] is not None:
        # yfinanceでは設備投資がマイナス表示のことが多いので加算する。
        return cfo[0] + capex[0] if capex[0] < 0 else cfo[0] - capex[0]
    return None


def _balance_latest(balance: pd.DataFrame, names: list[str]) -> float | None:
    values = _ordered_values(_row(balance, names))
    return values[0] if values else None


def get_company_snapshot(ticker: str) -> dict[str, Any] | None:
    symbol = f"{ticker}.T"

    try:
        stock = yf.Ticker(symbol)
        income = stock.financials
        cashflow = stock.cashflow
        balance = stock.balance_sheet
    except Exception:
        return None

    if income is None or income.empty:
        return None

    revenue = _ordered_values(_row(income, ["Total Revenue", "Operating Revenue"]))
    operating_income = _ordered_values(_row(income, ["Operating Income"]))
    eps = _ordered_values(_row(income, ["Diluted EPS", "Basic EPS"]))

    revenue_growth = _growth(
        revenue[0] if len(revenue) > 0 else None,
        revenue[1] if len(revenue) > 1 else None,
    )
    op_growth_latest = _growth(
        operating_income[0] if len(operating_income) > 0 else None,
        operating_income[1] if len(operating_income) > 1 else None,
    )
    op_growth_previous = _growth(
        operating_income[1] if len(operating_income) > 1 else None,
        operating_income[2] if len(operating_income) > 2 else None,
    )
    eps_growth = _growth(
        eps[0] if len(eps) > 0 else None,
        eps[1] if len(eps) > 1 else None,
    )

    latest_revenue = revenue[0] if len(revenue) > 0 else None
    previous_revenue = revenue[1] if len(revenue) > 1 else None
    latest_op = operating_income[0] if len(operating_income) > 0 else None
    previous_op = operating_income[1] if len(operating_income) > 1 else None

    latest_margin = (
        latest_op / latest_revenue * 100.0
        if latest_op is not None and latest_revenue not in (None, 0)
        else None
    )
    previous_margin = (
        previous_op / previous_revenue * 100.0
        if previous_op is not None and previous_revenue not in (None, 0)
        else None
    )
    margin_change = (
        latest_margin - previous_margin
        if latest_margin is not None and previous_margin is not None
        else None
    )

    company_name = symbol
    shares_outstanding = None
    market_cap = None
    try:
        info = stock.info or {}
        company_name = info.get("longName") or info.get("shortName") or symbol
        shares_outstanding = _num(info.get("sharesOutstanding"))
        market_cap = _num(info.get("marketCap"))
    except Exception:
        info = {}

    if shares_outstanding is None:
        try:
            shares_outstanding = _num(stock.fast_info.get("shares"))
        except Exception:
            pass

    cash = _balance_latest(
        balance,
        [
            "Cash Cash Equivalents And Short Term Investments",
            "Cash And Cash Equivalents",
            "Cash",
        ],
    )
    total_debt = _balance_latest(balance, ["Total Debt"])
    net_debt = None
    if total_debt is not None or cash is not None:
        net_debt = (total_debt or 0.0) - (cash or 0.0)

    fcf = _free_cash_flow(cashflow)
    market_price = _latest_price(stock)

    fiscal_period = None
    try:
        if len(income.columns) > 0:
            fiscal_period = pd.Timestamp(income.columns[0]).date().isoformat()
    except Exception:
        pass

    fields_for_coverage = {
        "revenue_growth_pct": revenue_growth,
        "operating_profit_growth_pct": op_growth_latest,
        "eps_growth_pct": eps_growth,
        "operating_margin_pct": latest_margin,
        "previous_growth_pct": op_growth_previous,
        "margin_change_points": margin_change,
        "market_price": market_price,
        "free_cash_flow": fcf,
        "net_debt": net_debt,
        "shares_outstanding": shares_outstanding,
    }
    available = sum(v is not None for v in fields_for_coverage.values())
    coverage = round(available / len(fields_for_coverage) * 100.0, 1)
    missing_fields = [k for k, v in fields_for_coverage.items() if v is None]

    return {
        "ticker": ticker,
        "symbol": symbol,
        "company_name": company_name,
        "fiscal_period": fiscal_period,
        "data_status": "external_yfinance",
        "data_source": "Yahoo Finance via yfinance",
        "revenue_growth_pct": revenue_growth,
        "operating_profit_growth_pct": op_growth_latest,
        "eps_growth_pct": eps_growth,
        "operating_margin_pct": latest_margin,
        "latest_growth_pct": op_growth_latest,
        "previous_growth_pct": op_growth_previous,
        "margin_change_points": margin_change,
        # yfinanceだけでは会社予想の上方修正率を安定取得できないため、欠損を明示する。
        "guidance_revision_pct": None,
        "guidance_revision_available": False,
        "market_price": market_price,
        "market_cap": market_cap,
        "base_free_cash_flow": fcf,
        "net_debt": net_debt,
        "shares_outstanding": shares_outstanding,
        "data_coverage_pct": coverage,
        "missing_fields": missing_fields,
    }
