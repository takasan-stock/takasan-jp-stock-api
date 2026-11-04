"""Local validation boundaries for existing Hunter request contracts."""

import pandas as pd

from market_data_normalizer import DataQualityError, pretrade_ohlcv
from pretrade import build_technical_snapshot


TECHNICAL_FIELDS = (
    "price", "ema20", "sma50", "sma200", "sma200_rising", "stage2", "stage4",
    "high52", "rs_proxy", "volume_ratio", "ema20_gap_pct", "breakout20", "pullback20",
    "gap_pct", "change_pct", "atr14", "avg_turnover20",
)


def build_pretrade_input(bars, calendar, latest_trade_date):
    if latest_trade_date not in calendar.trading_dates:
        return {"data_ok": False, "reason": "Expected latest session is not covered"}
    if len({bar.ticker for bar in bars}) > 1 or len({bar.trade_date for bar in bars}) != len(bars):
        raise DataQualityError("Mixed issue codes or duplicate daily bars")
    expected = [day for day in calendar.trading_dates if day <= latest_trade_date][-252:]
    window = sorted((bar for bar in bars if bar.trade_date <= latest_trade_date),
                    key=lambda bar: bar.trade_date)[-252:]
    if len(expected) != 252 or [bar.trade_date for bar in window] != expected:
        return {"data_ok": False, "reason": "Incomplete confirmed 252-session window"}
    frame = pd.DataFrame(pretrade_ohlcv(window)).set_index("trade_date")
    if (frame.isna().any().any() or (frame[["Open", "High", "Low", "Close"]] <= 0).any().any()
            or (frame["Volume"] < 0).any()):
        return {"data_ok": False, "reason": "Missing or invalid adjusted OHLCV"}
    technical = build_technical_snapshot(frame)
    technical["rs_proxy"] = None
    missing = [name for name in TECHNICAL_FIELDS if name != "rs_proxy" and technical.get(name) is None]
    if not technical["data_ok"] or missing:
        return {"data_ok": False, "reason": "Incomplete technical inputs", "missing_fields": missing}
    return {"data_ok": True, **{name: technical[name] for name in TECHNICAL_FIELDS}}
