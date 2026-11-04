"""Server-to-server Pre-Trade using confirmed J-Quants daily data."""

import json
import logging
import os
import secrets
from dataclasses import asdict
from datetime import datetime, time, timedelta
from functools import lru_cache
from time import monotonic
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import Field

from jquants_client import JQuantsClient, JQuantsError, JQuantsRequestMetrics, request_metrics
from jquants_pretrade_adapter import build_pretrade_input
from market_data_normalizer import DataQualityError, JST
from v3_api import pre_trade
from v3_contracts import PreTradeRequest, V3Response, unavailable_response


router = APIRouter()
SOURCE = "jquants-v2/daily"
ROUTE = "/internal/v3/pre-trade"
logger = logging.getLogger("uvicorn.error")


class JQuantsPreTradeRequest(PreTradeRequest):
    ticker: str = Field(pattern=r"^[0-9A-Z]{4}$")


def require_sites_service(authorization: str | None = Header(default=None)):
    expected = os.environ.get("SITES_PRETRADE_SERVICE_TOKEN", "")
    if not expected.strip():
        raise HTTPException(status_code=503, detail="Pre-Trade service unavailable")
    supplied = authorization or ""
    if not secrets.compare_digest(supplied.encode(), f"Bearer {expected}".encode()):
        raise HTTPException(status_code=401, detail="Service authentication required")


@lru_cache(maxsize=1)
def _client():
    key = os.environ.get("JQUANTS_PRETRADE_API_KEY", "").strip()
    if not key:
        return None
    return JQuantsClient(key)


def _now():
    return datetime.now(JST)


def _unavailable(reason, as_of=None):
    return unavailable_response(source=SOURCE, as_of=as_of, reason=f"insufficient_input: {reason}")


@router.post(
    ROUTE, response_model=V3Response[dict],
    dependencies=[Depends(require_sites_service)], include_in_schema=False,
)
def jquants_pretrade(request: JQuantsPreTradeRequest):
    correlation_id = uuid4().hex
    started = monotonic()
    metrics = JQuantsRequestMetrics()
    context = request_metrics.set(metrics)
    fields = {"correlation_id": correlation_id, "route": ROUTE, "ticker": request.ticker}
    outcome = {"classification": "internal_error", "status_class": "5xx"}
    logger.info("%s", json.dumps({**fields, "event": "pretrade_start"}))
    try:
        return _evaluate(request, outcome)
    except Exception:
        outcome.update(classification="internal_error", status_class="5xx", category="internal_error")
        raise HTTPException(status_code=500, detail="Pre-Trade evaluation failed") from None
    finally:
        request_metrics.reset(context)
        logger.info("%s", json.dumps({
            **fields, **outcome, **asdict(metrics), "event": "pretrade_end",
            "elapsed_ms": round((monotonic() - started) * 1000),
        }))


def _evaluate(request, outcome):
    outcome.update(classification="insufficient_input", status_class="2xx")
    client = _client()
    if client is None:
        outcome["category"] = "dedicated_key_unconfigured"
        return _unavailable("日足データ取得が未設定です")
    now = _now()
    today = now.date()
    end = today if now.time() >= time(16, 30) else today - timedelta(days=1)
    start = end - timedelta(days=450)
    try:
        calendar = client.calendar(start, end + timedelta(days=1))
        latest = calendar.previous_trading_day(end + timedelta(days=1))
        if latest is None:
            return _unavailable("確定営業日が未取得です")
        instrument = client.instrument(request.ticker, latest)
        result = client.daily(instrument, start, latest)
        if result.quality.stale:
            return _unavailable("日足キャッシュが古く、最新データを確認できません", latest.isoformat())
        technical = build_pretrade_input(result.bars, calendar, latest)
    except JQuantsError as error:
        outcome["category"] = "jquants_request_failure"
        outcome["upstream_status_class"] = (
            f"{error.status // 100}xx" if error.status is not None else "no_status"
        )
        return _unavailable("確定日足または取引カレンダーを確認できません")
    except DataQualityError:
        outcome["category"] = "jquants_data_quality"
        return _unavailable("確定日足または取引カレンダーを確認できません")
    if not technical.get("data_ok"):
        return _unavailable("最新の252営業日分の調整済み日足が不足しています", latest.isoformat())
    payload = request.model_dump(exclude={"ticker", "source", "as_of", "technical"})
    result = pre_trade(PreTradeRequest(
        **payload, source=SOURCE, as_of=latest.isoformat(), technical=technical,
    ))
    outcome.update(classification="evaluated", technical_data_ok=True, rs_proxy=None)
    return result
