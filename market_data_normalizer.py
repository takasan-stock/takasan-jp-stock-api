import math
import re
from bisect import bisect_right
from datetime import date, datetime, time, timedelta, timezone
from types import MappingProxyType

from market_data_types import DailyBar, FinancialDisclosure, Instrument, TradingCalendar, TradingDay


JST = timezone(timedelta(hours=9))
BAR_FIELDS = dict(zip(
    ("open", "high", "low", "close", "volume", "turnover", "adj_open",
     "adj_high", "adj_low", "adj_close", "adj_volume", "adjustment_factor"),
    ("O", "H", "L", "C", "Vo", "Va", "AdjO", "AdjH", "AdjL", "AdjC",
     "AdjVo", "AdjFactor"),
))
FINANCIAL_NUMBERS = (
    "Sales", "OP", "OdP", "NP", "EPS", "DEPS", "TA", "Eq", "EqAR", "BPS",
    "CFO", "CFI", "CFF", "CashEq", "ShOutFY", "TrShFY", "AvgSh",
    "FSales", "FOP", "FOdP", "FNP", "FEPS",
)


class DataQualityError(ValueError):
    pass


def ticker_code(value: str) -> str:
    code = value.strip().upper()
    if not re.fullmatch(r"[0-9A-Z]{4,5}", code):
        raise DataQualityError("Invalid issue code")
    return code


def _date(value):
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        raise DataQualityError("Missing or invalid market data date") from None


def _text(row, field):
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise DataQualityError(f"Missing or invalid {field}")
    return value.strip()


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def resolve_instrument(requested: str, rows, as_of: date) -> Instrument:
    requested = ticker_code(requested)
    expected = requested + "0" if len(requested) == 4 else requested
    matches = set()
    for row in rows:
        if row.get("Code") != expected:
            continue
        effective = _date(row.get("Date"))
        if effective <= as_of:
            matches.add(Instrument(
                requested, expected, _text(row, "CoName"), _text(row, "Mkt"), effective
            ))
    if not matches:
        raise DataQualityError("No eligible issue master match")
    latest = max(item.effective_date for item in matches)
    current = [item for item in matches if item.effective_date == latest]
    if len(current) != 1:
        raise DataQualityError("Conflicting issue master rows")
    return current[0]


def _check_issue(row, instrument):
    if row.get("Code") != instrument.jquants_code:
        raise DataQualityError("Unexpected issue code")


def _deduplicate(items, key):
    unique = {}
    for item in items:
        identity = key(item)
        if identity in unique and unique[identity] != item:
            raise DataQualityError("Conflicting duplicate market data rows")
        unique[identity] = item
    return tuple(unique.values())


def normalize_daily(rows, instrument: Instrument) -> tuple[DailyBar, ...]:
    bars = []
    for row in rows:
        _check_issue(row, instrument)
        bars.append(DailyBar(
            instrument.requested_ticker, _date(row.get("Date")),
            **{name: _number(row.get(field)) for name, field in BAR_FIELDS.items()},
        ))
    return tuple(sorted(
        _deduplicate(bars, lambda bar: (bar.ticker, bar.trade_date)),
        key=lambda bar: bar.trade_date,
    ))


def normalize_calendar(rows, start: date, end: date) -> TradingCalendar:
    parsed = []
    for row in rows:
        day = _date(row.get("Date"))
        division = row.get("HolDiv")
        if not isinstance(division, str) or division not in ("0", "1", "2", "3"):
            raise DataQualityError("Unknown trading calendar holiday division")
        if not start <= day <= end:
            raise DataQualityError("Trading calendar date outside requested range")
        parsed.append(TradingDay(day, division, division in ("1", "2")))
    days = tuple(sorted(_deduplicate(parsed, lambda day: day.date), key=lambda day: day.date))
    if len(days) != (end - start).days + 1:
        raise DataQualityError("Incomplete trading calendar")
    return TradingCalendar(start, end, days, tuple(day.date for day in days if day.is_trading_day))


def normalize_financials(rows, instrument: Instrument, trading_dates) -> tuple[FinancialDisclosure, ...]:
    calendar = sorted(set(trading_dates))
    disclosures = []
    for row in rows:
        _check_issue(row, instrument)
        disclosed = _date(row.get("DiscDate"))
        try:
            disclosed_time = time.fromisoformat(row["DiscTime"]) if row.get("DiscTime") else None
        except (ValueError, TypeError):
            raise DataQualityError("Invalid disclosure time") from None
        position = bisect_right(calendar, disclosed)
        disclosures.append(FinancialDisclosure(
            ticker=instrument.requested_ticker,
            disclosure_date=disclosed,
            disclosure_time=disclosed_time,
            disclosure_timestamp=datetime.combine(disclosed, disclosed_time, JST) if disclosed_time else None,
            disclosure_number=_text(row, "DiscNo"),
            document_type=_text(row, "DocType"),
            accounting_period=row.get("CurPerType") or None,
            period_start=_date(row["CurPerSt"]) if row.get("CurPerSt") else None,
            period_end=_date(row["CurPerEn"]) if row.get("CurPerEn") else None,
            raw=MappingProxyType(dict(row)),
            normalized=MappingProxyType({field: _number(row.get(field)) for field in FINANCIAL_NUMBERS}),
            available_date=calendar[position] if position < len(calendar) else None,
        ))
    return tuple(sorted(
        _deduplicate(disclosures, lambda item: item.revision_identity),
        key=lambda item: (item.disclosure_date, item.disclosure_time or time.min, item.disclosure_number),
    ))


def me_market_rows(bars):
    return tuple({"ticker": bar.ticker, "trade_date": bar.trade_date.isoformat(),
                  "adj_close": bar.adj_close} for bar in bars if bar.adj_close is not None)


def pretrade_ohlcv(bars):
    return tuple({"trade_date": bar.trade_date.isoformat(), "Open": bar.adj_open,
                  "High": bar.adj_high, "Low": bar.adj_low, "Close": bar.adj_close,
                  "Volume": bar.adj_volume} for bar in bars)


def entry_daily_context(bars, session_date: date, trading_dates):
    previous_date = max((day for day in trading_dates if day < session_date), default=None)
    previous = next((bar for bar in bars if bar.trade_date == previous_date), None)
    return {"prev_close": previous.close if previous else None,
            "prev_high": previous.high if previous else None}
