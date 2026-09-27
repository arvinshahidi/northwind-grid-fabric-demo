"""
Simulated "KQL Copilot": a small rule-based natural-language -> filter
compiler plus a canned KQL string, so the query box demos the Fabric
Copilot experience without calling any external LLM.
"""
from __future__ import annotations

import re


CANNED = [
    {
        "match": [r"feeders?.*(above|over|>)\s*(\d+)%?\s*load"],
        "kql_template": (
            "grid_telemetry\n"
            "| where element_type == 'feeder' and loading_pct > {threshold}\n"
            "| summarize arg_max(event_time_utc, *) by element_id\n"
            "| project element_id, region_id, loading_pct, voltage_kv, status"
        ),
        "default_threshold": 80,
        "label": "Feeders above threshold loading",
    },
    {
        "match": [r"wind farms?.*(dropped|drop).*(\d+)%?"],
        "kql_template": (
            "generation_telemetry\n"
            "| where asset_type == 'wind_turbine'\n"
            "| summarize now=arg_max(event_time_utc, active_power_mw) by farm_or_plant_id\n"
            "| join kind=inner (generation_telemetry\n"
            "    | where event_time_utc < ago(15m)\n"
            "    | summarize baseline=avg(active_power_mw) by farm_or_plant_id) on farm_or_plant_id\n"
            "| extend drop_pct = (baseline - now) / baseline * 100.0\n"
            "| where drop_pct > {threshold}"
        ),
        "default_threshold": 20,
        "label": "Wind farms with output drop",
    },
    {
        "match": [r"critical.*(customers?|facilit).*outag"],
        "kql_template": (
            "meter_telemetry\n"
            "| where last_gasp == true\n"
            "| join kind=inner crm_context on meter_id\n"
            "| where critical_facility == true\n"
            "| project meter_id, feeder_id, address, event_time_utc"
        ),
        "label": "Critical customers on currently outaged feeders",
    },
    {
        "match": [r"regions?.*(predicted|forecast).*(deficien|deficit|shortfall)"],
        "kql_template": (
            "predictions\n"
            "| where horizon_minutes == 240 and deficit_pct >= 15\n"
            "| where scope_type in ('region', 'grid')\n"
            "| project scope_id, deficit_pct, deficit_mw, reserve_margin_pct, event_time_utc"
        ),
        "label": "Regions with predicted energy deficiencies during peak",
    },
    {
        "match": [r"peaker.*(headroom)", r"reserve margin.*peaker"],
        "kql_template": (
            "generation_telemetry\n"
            "| where asset_type == 'gas_peaker'\n"
            "| summarize running_mw=sumif(active_power_mw, status=='running'),\n"
            "           nameplate_mw=sum(nameplate_mw) by bin(event_time_utc, 5m)\n"
            "| extend headroom_mw = nameplate_mw - running_mw"
        ),
        "label": "Available peaker headroom vs reserve margin",
    },
]


def _extract_threshold(q: str, default: float | None) -> float:
    m = re.search(r"(\d+(\.\d+)?)\s*%", q)
    if m:
        return float(m.group(1))
    return default if default is not None else 0.0


def answer_query(q: str, controller) -> dict:
    ql = q.lower()
    for entry in CANNED:
        for pattern in entry["match"]:
            if re.search(pattern, ql):
                threshold = _extract_threshold(ql, entry.get("default_threshold"))
                kql = entry["kql_template"].format(threshold=threshold)
                result = _execute_against_snapshot(entry["label"], controller, threshold)
                return {
                    "question": q, "matched_intent": entry["label"], "kql": kql,
                    "result_preview": result,
                }
    return {
        "question": q,
        "matched_intent": None,
        "kql": "// no canned pattern matched -- try one of the example questions in the Copilot panel",
        "result_preview": [],
    }


def _execute_against_snapshot(label: str, controller, threshold: float):
    """Best-effort live answer against the current in-memory snapshot, so the
    canned KQL isn't just decorative -- it reflects the live demo state."""
    snap = controller.snapshot() if controller.world else {}
    if label == "Feeders above threshold loading":
        replay = controller.bus.replay.get("grid_telemetry", [])
        latest_by_feeder = {}
        for e in replay:
            if e.get("element_type") == "feeder":
                latest_by_feeder[e["element_id"]] = e
        return [e for e in latest_by_feeder.values() if e["loading_pct"] > threshold]
    if label == "Regions with predicted energy deficiencies during peak":
        return [p for p in snap.get("predictions", []) if p.get("horizon_minutes") == 240 and p.get("deficit_pct", 0) >= 15]
    if label == "Critical customers on currently outaged feeders":
        replay = controller.bus.replay.get("meter_telemetry", [])
        return [e for e in replay if e.get("last_gasp") and e.get("critical_facility")]
    return []
