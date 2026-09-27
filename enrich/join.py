"""
Phase 2 - enrichment: join generation telemetry to ERP asset metadata, and
meter telemetry to CRM customer metadata. Pure dict joins, no external deps,
mirroring what an Eventstream "enrich with reference data" step would do.
"""
from __future__ import annotations


def enrich_generation(gen_events: list[dict], erp_by_asset: dict) -> list[dict]:
    out = []
    for e in gen_events:
        asset = erp_by_asset.get(e["asset_id"], {})
        out.append({
            **e,
            "nameplate_mw": asset.get("nameplate_mw"),
            "oem": asset.get("oem"),
            "install_year": asset.get("install_year"),
            "maintenance_due": asset.get("next_maintenance"),
        })
    return out


def enrich_meters(meter_events: list[dict], crm_by_meter: dict) -> list[dict]:
    out = []
    for e in meter_events:
        cust = crm_by_meter.get(e["meter_id"], {})
        out.append({
            **e,
            "customer_id": cust.get("customer_id"),
            "rate_class": cust.get("rate_class"),
            "critical_facility": cust.get("critical_facility"),
            "medical_baseline": cust.get("medical_baseline"),
        })
    return out


def build_erp_index(erp_rows: list[dict]) -> dict:
    return {r["asset_id"]: r for r in erp_rows}


def build_crm_index(crm_rows: list[dict]) -> dict:
    return {r["meter_id"]: r for r in crm_rows}
