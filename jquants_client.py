import time
from collections import OrderedDict, deque
from concurrent.futures import Future
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from threading import Lock

import requests

from market_data_normalizer import (
    BAR_FIELDS, DataQualityError, normalize_daily, normalize_financials,
    resolve_instrument, ticker_code,
)
from market_data_types import DailyBar, DataQuality, Instrument


class JQuantsError(RuntimeError):
    def __init__(self, reason, status=None, *, retryable=False):
        self.status = status
        self.retryable = retryable
        super().__init__(reason if status is None else f"{reason} (HTTP {status})")


class LightRateLimiter:
    """Share one limiter across clients using the same account in a process."""

    def __init__(self, *, clock=time.monotonic, sleep=time.sleep):
        self._clock, self._sleep = clock, sleep
        self._buckets = {"general": deque(), "financial": deque()}
        self._lock = Lock()

    def acquire(self, bucket):
        while True:
            with self._lock:
                now = self._clock()
                stamps = self._buckets[bucket]
                while stamps and now - stamps[0] >= 60:
                    stamps.popleft()
                if len(stamps) < 60:
                    stamps.append(now)
                    return
                delay = 60 - (now - stamps[0])
            self._sleep(delay)


_LIGHT_LIMITER = LightRateLimiter()
_DAILY = "/v2/equities/bars/daily"
_MASTER = "/v2/equities/master"
_FINANCIAL = "/v2/fins/summary"


@dataclass(frozen=True)
class DailyResult:
    bars: tuple[DailyBar, ...]
    quality: DataQuality
    cache_status: str


@dataclass(frozen=True)
class _Cached:
    value: object
    fetched_at: datetime
    timestamp: float


class JQuantsClient:
    def __init__(
        self, api_key: str, *, get=requests.get, limiter=_LIGHT_LIMITER,
        clock=time.monotonic, sleep=time.sleep, now=lambda: datetime.now(timezone.utc),
        timeout=10, retries=2, max_pages=100, ttl=900, stale_ttl=3600, cache_size=128,
    ):
        if not api_key.strip() or retries < 0 or max_pages < 1 or cache_size < 1:
            raise ValueError("Invalid J-Quants client configuration")
        if timeout <= 0 or ttl <= 0 or stale_ttl < ttl:
            raise ValueError("Invalid J-Quants timing configuration")
        self._api_key, self._get, self._limiter = api_key.strip(), get, limiter
        self._clock, self._sleep, self._now = clock, sleep, now
        self._timeout, self._retries, self._max_pages = timeout, retries, max_pages
        self._ttl, self._stale_ttl, self._cache_size = ttl, stale_ttl, cache_size
        self._cache = OrderedDict()
        self._pending = {}
        self._lock = Lock()

    def _page(self, endpoint, params):
        bucket = "financial" if endpoint == _FINANCIAL else "general"
        for attempt in range(self._retries + 1):
            self._limiter.acquire(bucket)
            retry_after = 0
            try:
                response = self._get(
                    "https://api.jquants.com" + endpoint, params=params,
                    headers={"x-api-key": self._api_key}, timeout=self._timeout,
                    allow_redirects=False,
                )
            except (requests.Timeout, TimeoutError):
                error = JQuantsError("J-Quants request timed out", retryable=True)
            except requests.RequestException:
                error = JQuantsError("J-Quants transport failure", retryable=True)
            else:
                status = response.status_code
                if 200 <= status < 300:
                    try:
                        payload = response.json()
                    except ValueError:
                        raise JQuantsError("Invalid or empty J-Quants JSON") from None
                    if (not isinstance(payload, dict) or not isinstance(payload.get("data"), list)
                            or any(not isinstance(row, dict) for row in payload["data"])):
                        raise JQuantsError("Invalid J-Quants data envelope")
                    return payload
                error = JQuantsError("J-Quants request failed", status, retryable=status == 429 or status >= 500)
                if status == 429:
                    header = response.headers.get("Retry-After", "").strip()
                    if header.isascii() and header.isdecimal():
                        retry_after = float(header)
            if not error.retryable or attempt == self._retries or retry_after > 60:
                raise error
            self._sleep(max(retry_after, min(2 ** attempt, 30)))

    def _rows(self, endpoint, params):
        rows, seen = [], set()
        for _ in range(self._max_pages):
            page = self._page(endpoint, params)
            rows.extend(page["data"])
            cursor = page.get("pagination_key")
            if cursor is None or cursor == "":
                return rows
            if not isinstance(cursor, str) or cursor in seen:
                raise JQuantsError("Invalid or repeated J-Quants pagination key")
            seen.add(cursor)
            params = {**params, "pagination_key": cursor}
        raise JQuantsError("J-Quants pagination limit exceeded")

    def _cached(self, key, fetch, *, allow_stale=False):
        with self._lock:
            old = self._cache.get(key)
            if old and self._clock() - old.timestamp < self._ttl:
                self._cache.move_to_end(key)
                return old, "hit"
            pending = self._pending.get(key)
            leader = pending is None
            if leader:
                pending = self._pending[key] = Future()
        if not leader:
            value, status = pending.result()
            return value, "stale" if status == "stale" else "shared"
        try:
            try:
                value = _Cached(fetch(), self._now(), self._clock())
            except JQuantsError as error:
                if not (allow_stale and error.retryable and old
                        and self._clock() - old.timestamp < self._stale_ttl):
                    raise
                result = old, "stale"
            else:
                with self._lock:
                    self._cache[key] = value
                    self._cache.move_to_end(key)
                    if len(self._cache) > self._cache_size:
                        self._cache.popitem(last=False)
                result = value, "miss"
            pending.set_result(result)
            return result
        except BaseException as error:
            pending.set_exception(error)
            raise
        finally:
            with self._lock:
                del self._pending[key]

    def instrument(self, ticker: str, as_of: date) -> Instrument:
        code = ticker_code(ticker)
        params = {"code": code, "date": as_of.isoformat()}
        value, _ = self._cached(
            (_MASTER, code, as_of),
            lambda: resolve_instrument(code, self._rows(_MASTER, params), as_of),
        )
        return value.value

    def daily(self, instrument: Instrument, start: date, end: date) -> DailyResult:
        if start > end:
            raise ValueError("Invalid daily date range")
        code = instrument.jquants_code
        params = {"code": code, "from": start.isoformat(), "to": end.isoformat()}

        def fetch():
            canonical = Instrument(code, code, instrument.name, instrument.market, instrument.effective_date)
            bars = normalize_daily(self._rows(_DAILY, params), canonical)
            if any(not start <= bar.trade_date <= end for bar in bars):
                raise DataQualityError("Daily bar outside requested range")
            return bars

        cached, status = self._cached((_DAILY, code, start, end), fetch, allow_stale=True)
        bars = tuple(replace(bar, ticker=instrument.requested_ticker) for bar in cached.value)
        latest = bars[-1].trade_date if bars else None
        missing = tuple(field for field in BAR_FIELDS if not bars or any(
            getattr(bar, field) is None for bar in bars
        ))
        coverage = (sum(getattr(bar, field) is not None for bar in bars for field in BAR_FIELDS)
                    / (len(bars) * len(BAR_FIELDS))) if bars else 0.0
        stale = status == "stale"
        return DailyResult(bars, DataQuality(
            "jquants-v2", latest, latest, cached.fetched_at, coverage, stale, missing,
            "stale" if stale else "empty" if not bars else "partial" if missing else "complete",
        ), status)

    def financials(self, instrument: Instrument, trading_dates):
        code = instrument.jquants_code
        cached, _ = self._cached(
            (_FINANCIAL, code), lambda: self._rows(_FINANCIAL, {"code": code})
        )
        return normalize_financials(cached.value, instrument, trading_dates)
