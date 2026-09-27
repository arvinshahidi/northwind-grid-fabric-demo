"""
Phase 4 - Activator stand-in: a small rule engine that evaluates the exact
Microsoft-worded rules against live grid state + predictions and emits
activator_alerts. Every action is labeled "recommended" - nothing here
closes a breaker automatically.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass


RULES = {
    "RULE-DEMAND-90": {
        "description": "Demand exceeds 90% of available capacity",
        "severity": "critical",
        "action": "Recommend: start additional peakers, discharge BESS, initiate commercial demand response.",
    },
    "RULE-DEFICIT-15": {
        "description": "Forecast 15% supply shortfall during peak evening hours",
        "severity": "warning",
        "action": "Recommend: schedule additional generation and/or a demand-response event ahead of peak.",
    },
    "RULE-RENEWABLE-DROP": {
        "description": "Renewable generation drop from weather",
        "severity": "critical",
        "action": "Recommend: emergency redispatch; evaluate load-shed only if reserve margin breaches the hard threshold.",
    },
    "RULE-REDISTRIBUTE": {
        "description": "Redistribute from surplus region to deficit region",
        "severity": "warning",
        "action": "Recommend: schedule inter-region transfer / redispatch from surplus region.",
    },
    "RULE-FEEDER-OVERLOAD": {
        "description": "Feeder loading exceeds 90% of thermal rating",
        "severity": "warning",
        "action": "Recommend: field crew inspection and/or feeder load transfer.",
    },
    "RULE-CRITICAL-OUTAGE": {
        "description": "Critical facility customers on an outaged feeder",
        "severity": "critical",
        "action": "Recommend: prioritize crew dispatch to restore feeder serving critical/medical customers.",
    },
}


def _mk_alert(rule_id: str, scope_type: str, scope_id: str, metric: str, value: float,
              threshold: float, event_time_utc: str) -> dict:
    rule = RULES[rule_id]
    return {
        "alert_id": str(uuid.uuid4()), "event_time_utc": event_time_utc, "rule_id": rule_id,
        "severity": rule["severity"], "scope_type": scope_type, "scope_id": scope_id,
        "metric": metric, "value": round(value, 2), "threshold": threshold,
        "message": rule["description"], "recommended_action": rule["action"],
        "human_approval_required": True, "status": "open",
    }


def evaluate_rules(grid_summary: dict, predictions: list[dict], grid_events: list[dict],
                    meter_events: list[dict], wind_drop_by_farm: dict[str, float],
                    event_time_utc: str, alert_state: dict | None = None,
                    minute_of_run: float = 0.0, cooldown_minutes: float = 30.0) -> list[dict]:
    alert_state = alert_state if alert_state is not None else {}

    def _debounced(rule_id: str, scope_id: str) -> bool:
        """Returns True if this rule/scope should fire now (first time, or cooldown elapsed)."""
        key = (rule_id, scope_id)
        last = alert_state.get(key)
        if last is not None and minute_of_run - last < cooldown_minutes:
            return False
        alert_state[key] = minute_of_run
        return True

    alerts = []

    if grid_summary["demand_pct_of_capacity"] > 90 and _debounced("RULE-DEMAND-90", "GRID-NW"):
        alerts.append(_mk_alert("RULE-DEMAND-90", "grid", "GRID-NW", "demand_pct_of_capacity",
                                 grid_summary["demand_pct_of_capacity"], 90.0, event_time_utc))

    for p in predictions:
        if p["horizon_minutes"] == 240 and p["deficit_pct"] >= 15.0 and _debounced("RULE-DEFICIT-15", p["scope_id"]):
            alerts.append(_mk_alert("RULE-DEFICIT-15", p["scope_type"], p["scope_id"], "deficit_pct",
                                     p["deficit_pct"], 15.0, event_time_utc))

    for farm_id, drop_pct in wind_drop_by_farm.items():
        if drop_pct >= 25.0 and _debounced("RULE-RENEWABLE-DROP", farm_id):
            alerts.append(_mk_alert("RULE-RENEWABLE-DROP", "asset", farm_id, "wind_output_drop_pct",
                                     drop_pct, 20.0, event_time_utc))

    region_balance: dict[str, float] = {}
    for rid in grid_summary.get("demand_by_region", {}):
        gen = grid_summary.get("gen_by_region", {}).get(rid, 0.0)
        dem = grid_summary["demand_by_region"].get(rid, 0.0)
        region_balance[rid] = gen - dem
    # Only meaningful once the grid overall is under stress -- otherwise every
    # region naturally nets non-zero because generation siting != load siting.
    if grid_summary["reserve_margin_pct"] < 25 or grid_summary["demand_pct_of_capacity"] > 75:
        surplus_regions = [r for r, v in region_balance.items() if v > 40]
        deficit_regions = [r for r, v in region_balance.items() if v < -40]
        if surplus_regions and deficit_regions and _debounced("RULE-REDISTRIBUTE", "GRID-NW"):
            alerts.append(_mk_alert("RULE-REDISTRIBUTE", "grid", "GRID-NW", "region_imbalance_mw",
                                     max(abs(v) for v in region_balance.values()), 40.0, event_time_utc))

    feeder_overloads = [e for e in grid_events if e["element_type"] == "feeder" and e["loading_pct"] > 90]
    seen_feeders = set()
    for e in feeder_overloads:
        if e["element_id"] in seen_feeders or not _debounced("RULE-FEEDER-OVERLOAD", e["element_id"]):
            continue
        seen_feeders.add(e["element_id"])
        alerts.append(_mk_alert("RULE-FEEDER-OVERLOAD", "feeder", e["element_id"], "loading_pct",
                                 e["loading_pct"], 90.0, event_time_utc))

    outaged_feeders = {e["element_id"] for e in grid_events
                        if e["element_type"] == "feeder" and e["status"] == "outage"}
    if outaged_feeders:
        critical_hits = [m for m in meter_events if m.get("critical_facility") and m["feeder_id"] in outaged_feeders]
        if critical_hits and _debounced("RULE-CRITICAL-OUTAGE", "GRID-NW"):
            alerts.append(_mk_alert("RULE-CRITICAL-OUTAGE", "grid", "GRID-NW", "critical_customers_affected",
                                     len(critical_hits), 0, event_time_utc))

    return alerts
