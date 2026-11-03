from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Mapping


@dataclass(frozen=True)
class Instrument:
    requested_ticker: str
    jquants_code: str
    name: str
    market: str
    effective_date: date


@dataclass(frozen=True)
class DailyBar:
    ticker: str
    trade_date: date
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None
    turnover: float | None
    adj_open: float | None
    adj_high: float | None
    adj_low: float | None
    adj_close: float | None
    adj_volume: float | None
    adjustment_factor: float | None


@dataclass(frozen=True)
class FinancialDisclosure:
    ticker: str
    disclosure_date: date
    disclosure_time: time | None
    disclosure_timestamp: datetime | None
    disclosure_number: str
    document_type: str
    accounting_period: str | None
    period_start: date | None
    period_end: date | None
    raw: Mapping[str, object]
    normalized: Mapping[str, float | None]
    available_date: date | None

    @property
    def revision_identity(self) -> tuple[str, str]:
        return self.ticker, self.disclosure_number


@dataclass(frozen=True)
class DataQuality:
    source: str
    as_of: date | datetime | None
    latest_trade_date: date | None
    fetched_at: datetime
    coverage: float
    stale: bool
    missing_fields: tuple[str, ...]
    data_quality: str
