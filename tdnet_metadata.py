from __future__ import annotations

import hashlib
import json
import unicodedata
from datetime import datetime, timezone
from typing import Any, Iterable

from models import JQuantsComparisonInfo, TdnetDisclosureMetadata
from v11_foundation import V11_CONFIG


JQUANTS_COMPARISON_KIND = {
    "financial_results": "financial_results",
    "earnings_forecast_revision": "earnings_forecast",
    "dividend_forecast_revision": "dividend_forecast",
}


def _normalized_text(value: object) -> str:
    return "".join(unicodedata.normalize("NFKC", str(value)).split()).lower()


def _normalized_ticker(value: object) -> str:
    return str(value).strip().upper().removeprefix("TSE:").removesuffix(".T")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _content_fingerprint(value: Any | None) -> str | None:
    if value is None:
        return None
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def classify_tdnet_title(title: str, rules: dict | None = None) -> dict:
    tdnet_rules = rules or V11_CONFIG["tdnet"]
    normalized_title = _normalized_text(title)
    for rule in tdnet_rules["classification_rules"]:
        for keyword in rule["match_any"]:
            if _normalized_text(keyword) in normalized_title:
                return {
                    "category": rule["category"],
                    "category_label": rule["label"],
                    "importance": rule["importance"],
                    "matched_rule_keyword": keyword,
                }
    return {
        "category": "other",
        "category_label": "その他",
        "importance": "low",
        "matched_rule_keyword": None,
    }


def detect_tdnet_record_status(title: str, rules: dict | None = None) -> str:
    tdnet_rules = rules or V11_CONFIG["tdnet"]
    normalized_title = _normalized_text(title)
    if any(_normalized_text(keyword) in normalized_title for keyword in tdnet_rules["status_rules"]["deleted"]):
        return "deleted"
    if any(_normalized_text(keyword) in normalized_title for keyword in tdnet_rules["status_rules"]["correction"]):
        return "correction"
    return "active"


def _comparison_key(ticker: str, comparison_kind: str | None, fiscal_period: str | None) -> str | None:
    if not comparison_kind or not fiscal_period:
        return None
    return f"{ticker}|{comparison_kind}|{fiscal_period}"


def _deduplication_key(raw: dict, ticker: str) -> str:
    identity = {
        "ticker": ticker,
        "disclosed_at": str(raw.get("disclosed_at") or "").strip(),
        "title": _normalized_text(raw.get("title") or ""),
        "official_material_url": str(raw.get("official_material_url") or "").strip(),
    }
    return _content_fingerprint(identity) or ""


def build_tdnet_metadata(raw: dict, retrieved_at: datetime | str | None = None) -> TdnetDisclosureMetadata:
    title = str(raw.get("title") or "").strip()
    ticker = _normalized_ticker(raw.get("ticker") or "")
    classified = classify_tdnet_title(title)
    record_status = str(raw.get("record_status") or detect_tdnet_record_status(title))
    category = classified["category"]
    eligible = category in V11_CONFIG["tdnet"]["jquants_reflection_categories"] and record_status != "deleted"
    comparison_kind = JQUANTS_COMPARISON_KIND.get(category) if eligible else None
    fiscal_period = str(raw.get("fiscal_period") or "").strip() or None
    comparison_key = _comparison_key(ticker, comparison_kind, fiscal_period)
    tdnet_fingerprint = _content_fingerprint(raw.get("comparison_payload")) if eligible else None

    if not eligible:
        status = "not_applicable"
        reason = "This disclosure category is not reflected in J-Quants financial data."
    elif comparison_key is None or tdnet_fingerprint is None:
        status = "pending_comparison"
        reason = "A fiscal period and comparison payload are required before reflection can be assessed."
    else:
        status = "pending_comparison"
        reason = "No J-Quants comparison record has been supplied."

    retrieval_time = retrieved_at or raw.get("retrieved_at") or _utc_now()
    return TdnetDisclosureMetadata(
        disclosure_id=str(raw.get("disclosure_id") or "").strip(),
        disclosed_at=raw.get("disclosed_at"),
        ticker=ticker,
        company_name=str(raw.get("company_name") or "").strip(),
        title=title,
        category=category,
        category_label=classified["category_label"],
        importance=classified["importance"],
        official_material_url=str(raw.get("official_material_url") or "").strip(),
        record_status=record_status,
        supersedes_disclosure_id=str(raw.get("supersedes_disclosure_id") or "").strip() or None,
        retrieved_at=retrieval_time,
        matched_rule_keyword=classified["matched_rule_keyword"],
        deduplication_key=_deduplication_key(raw, ticker),
        jquants_comparison=JQuantsComparisonInfo(
            eligible=eligible,
            comparison_kind=comparison_kind,
            comparison_key=comparison_key,
            tdnet_content_fingerprint=tdnet_fingerprint,
            status=status,
            reason=reason,
        ),
    )


def assess_jquants_reflection(
    disclosure: TdnetDisclosureMetadata,
    jquants_record: dict | None,
) -> TdnetDisclosureMetadata:
    def updated_comparison(**updates: Any) -> JQuantsComparisonInfo:
        return JQuantsComparisonInfo(**{**comparison.model_dump(), **updates})

    comparison = disclosure.jquants_comparison
    if not comparison.eligible or disclosure.record_status == "deleted":
        updated = updated_comparison(
            eligible=False,
            status="not_applicable",
            reason="This disclosure category or record status is outside J-Quants financial reflection checks.",
        )
        return disclosure.model_copy(update={"jquants_comparison": updated})

    if comparison.comparison_key is None or comparison.tdnet_content_fingerprint is None:
        updated = updated_comparison(
            status="pending_comparison",
            reason="Comparison fields are incomplete; date alone is not sufficient.",
        )
        return disclosure.model_copy(update={"jquants_comparison": updated})

    if jquants_record is None:
        updated = updated_comparison(
            status="not_reflected",
            reason="No J-Quants record matched the eligible disclosure comparison key.",
        )
        return disclosure.model_copy(update={"jquants_comparison": updated})

    jquants_key = _comparison_key(
        _normalized_ticker(jquants_record.get("ticker") or ""),
        str(jquants_record.get("comparison_kind") or "").strip() or None,
        str(jquants_record.get("fiscal_period") or "").strip() or None,
    )
    jquants_fingerprint = _content_fingerprint(jquants_record.get("comparison_payload"))
    jquants_record_id = str(jquants_record.get("record_id") or "").strip() or None
    source_disclosure_id = str(jquants_record.get("source_disclosure_id") or "").strip() or None

    common_updates = {
        "jquants_record_id": jquants_record_id,
        "jquants_updated_at": jquants_record.get("updated_at"),
        "jquants_content_fingerprint": jquants_fingerprint,
    }
    if jquants_key != comparison.comparison_key:
        updated = updated_comparison(
            **common_updates,
            status="not_reflected",
            reason="J-Quants category, ticker, or fiscal-period key does not match the TDnet disclosure.",
        )
    elif source_disclosure_id == disclosure.disclosure_id or jquants_fingerprint == comparison.tdnet_content_fingerprint:
        updated = updated_comparison(
            **common_updates,
            status="reflected",
            reason="J-Quants matched the disclosure identity or comparison payload.",
        )
    else:
        updated = updated_comparison(
            **common_updates,
            status="not_reflected",
            reason="The comparison key matched, but the J-Quants payload did not match the TDnet disclosure.",
        )
    return disclosure.model_copy(update={"jquants_comparison": updated})


def deduplicate_and_apply_status(
    disclosures: Iterable[TdnetDisclosureMetadata],
) -> tuple[list[TdnetDisclosureMetadata], list[str]]:
    retained: dict[str, TdnetDisclosureMetadata] = {}
    order: list[str] = []
    duplicate_ids: list[str] = []

    for disclosure in disclosures:
        key = disclosure.deduplication_key
        if key in retained:
            duplicate_ids.append(disclosure.disclosure_id)
            if disclosure.retrieved_at > retained[key].retrieved_at:
                retained[key] = disclosure
            continue
        retained[key] = disclosure
        order.append(key)

    by_id = {item.disclosure_id: item for item in retained.values()}
    for item in list(retained.values()):
        target_id = item.supersedes_disclosure_id
        target = by_id.get(target_id) if target_id else None
        if target is None:
            continue
        if item.record_status == "correction":
            target_status = "corrected"
        elif item.record_status == "deleted":
            target_status = "deleted"
        else:
            continue
        updated_target = target.model_copy(update={"record_status": target_status})
        by_id[target.disclosure_id] = updated_target
        target_key = target.deduplication_key
        retained[target_key] = updated_target

    return [retained[key] for key in order], duplicate_ids
