from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path


CONFIG_PATH = Path(__file__).with_name("analysis_rules.v1.1.json")
REQUIRED_TRACE_FIELDS = {
    "matched_conditions",
    "failed_conditions",
    "missing_conditions",
}


def _load_and_validate() -> dict:
    with CONFIG_PATH.open(encoding="utf-8") as handle:
        config = json.load(handle)

    flags = config.get("feature_flags", {})
    if flags.get("tdnet_auto_fetch") is not False:
        raise RuntimeError("TDnet auto-fetch must remain disabled until JPX approval.")

    tdnet = config.get("tdnet", {})
    rules = tdnet.get("classification_rules", [])
    categories = [rule.get("category") for rule in rules]
    if not rules or any(not rule.get("match_any") for rule in rules) or len(categories) != len(set(categories)):
        raise RuntimeError("TDnet classification rules must have unique categories and match keywords.")

    eligible_categories = set(tdnet.get("jquants_reflection_categories", []))
    allowed_reflection_categories = {
        "financial_results",
        "earnings_forecast_revision",
        "dividend_forecast_revision",
    }
    if eligible_categories != allowed_reflection_categories:
        raise RuntimeError("J-Quants reflection checks must only cover supported financial disclosure categories.")

    balanced = config.get("cash_value_score", {}).get("profiles", {}).get("balanced", {})
    if not balanced or abs(sum(balanced.values()) - 1.0) > 1e-9:
        raise RuntimeError("Cash Value Score balanced weights must total 1.0.")
    scoring_components = set(config.get("cash_value_score", {}).get("scoring", {}))
    if set(balanced) != scoring_components:
        raise RuntimeError("Cash Value Score weights and scoring components must match.")

    growth_quality = config.get("growth_quality", {})
    if growth_quality.get("primary_metric") != "revenue_cagr_5y" or growth_quality.get("auxiliary_metric") != "eps_cagr_5y":
        raise RuntimeError("Growth Quality primary and auxiliary metrics do not match the v1.1 contract.")
    if "short_history" not in config.get("missing_data_statuses", []):
        raise RuntimeError("Growth Quality requires an explicit short_history status.")
    growth_weights = growth_quality.get("score", {}).get("weights", {})
    if not growth_weights or abs(sum(growth_weights.values()) - 1.0) > 1e-9:
        raise RuntimeError("Growth Quality Score weights must total 1.0.")

    coverage = config.get("coverage", {})
    thresholds = coverage.get("confidence_thresholds", {})
    if not 0 <= thresholds.get("medium_min_pct", -1) < thresholds.get("high_min_pct", -1) <= 100:
        raise RuntimeError("Coverage confidence thresholds are invalid.")
    roic_thresholds = coverage.get("roic_sanity_thresholds", {})
    if not 0 < roic_thresholds.get("high_review_pct", 0) < roic_thresholds.get("extreme_review_pct", 0):
        raise RuntimeError("ROIC sanity thresholds are invalid.")

    trace_fields = set(config.get("classification", {}).get("trace_fields", []))
    if trace_fields != REQUIRED_TRACE_FIELDS:
        raise RuntimeError("Classification trace fields do not match the v1.1 contract.")
    classification = config.get("classification", {})
    priority = classification.get("primary_priority", [])
    if set(priority) != {"main_candidate", "early_candidate", "momentum_candidate"} or len(priority) != 3:
        raise RuntimeError("Classification primary priority is invalid.")
    overheat = classification.get("thresholds", {}).get("overheat_risk", {})
    if not 1 <= overheat.get("required_points", 0) <= 4:
        raise RuntimeError("Overheat risk point threshold is invalid.")

    return config


V11_CONFIG = _load_and_validate()


def get_v11_public_metadata() -> dict:
    """Return additive compatibility metadata without enabling v1.1 calculations."""
    return {
        "rule_version": V11_CONFIG["version"],
        "status": V11_CONFIG["status"],
        "feature_flags": deepcopy(V11_CONFIG["feature_flags"]),
        "data_source_policy_version": V11_CONFIG["data_source_policy"]["version"],
    }
