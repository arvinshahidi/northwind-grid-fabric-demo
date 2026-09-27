"""
Export JSON Schema (Draft 2020-12) for every stream model, and print one
example payload per stream so the contract is visible at a glance.

Usage: python -m schemas.export_schemas
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

from schemas.models import (
    GenerationTelemetry, GridTelemetry, MeterTelemetry, CrmContext,
    ErpAssets, WeatherObservation, Prediction, ActivatorAlert,
)

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "json_schema")

MODELS = {
    "generation_telemetry": GenerationTelemetry,
    "grid_telemetry": GridTelemetry,
    "meter_telemetry": MeterTelemetry,
    "crm_context": CrmContext,
    "erp_assets": ErpAssets,
    "weather_observations": WeatherObservation,
    "predictions": Prediction,
    "activator_alerts": ActivatorAlert,
}


def export_all():
    os.makedirs(OUT_DIR, exist_ok=True)
    for name, model in MODELS.items():
        schema = model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"https://northwindgrid.example/schemas/{name}.json"
        schema["title"] = name
        with open(os.path.join(OUT_DIR, f"{name}.schema.json"), "w", encoding="utf-8") as f:
            json.dump(schema, f, indent=2)
    print(f"Wrote {len(MODELS)} JSON Schema (Draft 2020-12) files to {OUT_DIR}")


def example_payloads():
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    examples = {
        "generation_telemetry": GenerationTelemetry(
            event_id=str(uuid.uuid4()), event_time_utc=now_iso, event_time_local=now_iso,
            asset_id="GEN-003", asset_type="wind_turbine", farm_or_plant_id="WIND-FARM-01",
            region_id="REG-COASTAL", substation_id="SUB-01",
            active_power_mw=2.8, reactive_power_mvar=0.4, voltage_kv=34.4, frequency_hz=60.01,
            availability_pct=98.0, status="running", wind_speed_ms=9.2, irradiance_wm2=None,
            soc_pct=None, curtailment_mw=0.0, setpoint_mw=3.0,
        ),
        "grid_telemetry": GridTelemetry(
            event_id=str(uuid.uuid4()), event_time_utc=now_iso, event_time_local=now_iso,
            element_id="FDR-005", element_type="feeder", region_id="REG-METRO",
            load_mw=18.2, generation_in_mw=0.0, loading_pct=71.5, voltage_kv=13.7,
            frequency_hz=59.98, unbalance_pct=1.1, status="normal",
        ),
        "meter_telemetry": MeterTelemetry(
            event_id=str(uuid.uuid4()), event_time_utc=now_iso, event_time_local=now_iso,
            meter_id="MTR-00042", feeder_id="FDR-005", substation_id="SUB-02", region_id="REG-METRO",
            active_power_kw=4.3, voltage_v=239.5, current_a=18.0, power_factor=0.97,
            energy_kwh_delta=0.006, voltage_violation=False, last_gasp=False, restoration=False,
        ),
        "crm_context": CrmContext(
            event_time_utc=now_iso, meter_id="MTR-00042", customer_id="CUST-00042",
            address="412 Cedar Ave", postal_code="98204", lat=47.61, lon=-122.20,
            rate_class="RES", medical_baseline=False, critical_facility=False, feeder_id="FDR-005",
        ),
        "erp_assets": ErpAssets(
            as_of_utc=now_iso, asset_id="GEN-003", asset_type="wind_turbine", name="WIND Unit 3",
            nameplate_mw=3.2, voltage_kv=34.5, region_id="REG-COASTAL", substation_id="SUB-01",
            feeder_id="FDR-002", lat=47.02, lon=-124.1, install_year=2016,
            last_maintenance="2024-03-15", next_maintenance="2025-11-01", status="running", oem="Vestas",
        ),
        "weather_observations": WeatherObservation(
            event_time_utc=now_iso, region_id="REG-COASTAL", temp_c=14.2, wind_speed_ms=9.5,
            wind_dir_deg=250.0, ghi_wm2=0.0, cloud_cover_pct=80.0, precip_mm_h=0.2, alert=None,
        ),
        "predictions": Prediction(
            event_time_utc=now_iso, horizon_minutes=240, scope_type="grid", scope_id="GRID-NW",
            demand_mw_p50=902.0, generation_mw_p50=780.0, deficit_mw=122.0, deficit_pct=13.5,
            reserve_margin_pct=8.2, spike_probability=0.62,
        ),
        "activator_alerts": ActivatorAlert(
            alert_id=str(uuid.uuid4()), event_time_utc=now_iso, rule_id="RULE-DEMAND-90",
            severity="critical", scope_type="grid", scope_id="GRID-NW", metric="demand_pct_of_capacity",
            value=92.4, threshold=90.0, message="Demand exceeds 90% of available capacity",
            recommended_action="Start peakers GASPK-2/3, discharge BESS-1, initiate commercial DR",
        ),
    }
    for name, obj in examples.items():
        print(f"\n--- {name} example ---")
        print(obj.model_dump_json(indent=2))
    return examples


if __name__ == "__main__":
    export_all()
    example_payloads()
