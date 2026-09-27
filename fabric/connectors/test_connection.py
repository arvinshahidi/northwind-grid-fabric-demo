"""
One-shot connectivity test for a single Fabric Eventstream connection string.

Sends one synthetic test event and reports success/failure immediately, so
you can verify each Eventstream as you wire it in Day 2 -- without waiting
to run the full simulator.

Usage:
  # PowerShell
  $env:FABRIC_ES_GENERATION_TELEMETRY = "Endpoint=sb://...;...;EntityPath=es-generation-telemetry"
  .venv\\Scripts\\python.exe -m fabric.connectors.test_connection generation_telemetry

Or pass a raw connection string directly:
  .venv\\Scripts\\python.exe -m fabric.connectors.test_connection --conn-str "Endpoint=sb://...;...;EntityPath=es-x" --stream generation_telemetry
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

from fabric.connectors.eventstream_publisher import KNOWN_STREAMS, _env_var_for_stream


def build_sample_event(stream: str) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    samples = {
        "generation_telemetry": {
            "event_id": "test-evt-1", "event_time_utc": now, "event_time_local": now,
            "asset_id": "WIND-COASTAL-01", "asset_type": "wind_turbine", "farm_or_plant_id": "FARM-COASTAL-1",
            "region_id": "REGION-COASTAL", "substation_id": "SUB-01", "active_power_mw": 2.1,
            "reactive_power_mvar": 0.3, "voltage_kv": 34.5, "frequency_hz": 60.0, "availability_pct": 98.0,
            "status": "running", "wind_speed_ms": 9.5, "irradiance_wm2": None, "soc_pct": None,
            "curtailment_mw": 0.0, "setpoint_mw": 2.1,
        },
        "grid_telemetry": {
            "event_id": "test-evt-1", "event_time_utc": now, "event_time_local": now,
            "element_id": "FEEDER-COASTAL-01", "element_type": "feeder", "region_id": "REGION-COASTAL",
            "load_mw": 12.4, "generation_in_mw": 13.1, "loading_pct": 62.0, "voltage_kv": 34.5,
            "frequency_hz": 60.0, "unbalance_pct": 0.8, "status": "normal",
        },
        "meter_telemetry": {
            "event_id": "test-evt-1", "event_time_utc": now, "event_time_local": now,
            "meter_id": "MTR-000001", "feeder_id": "FEEDER-COASTAL-01", "substation_id": "SUB-01",
            "region_id": "REGION-COASTAL", "active_power_kw": 4.2, "voltage_v": 240.1, "current_a": 17.5,
            "power_factor": 0.98, "energy_kwh_delta": 0.006, "voltage_violation": False,
            "last_gasp": False, "restoration": False, "quality_flag": "good",
        },
        "crm_context": {
            "event_time_utc": now, "meter_id": "MTR-000001", "customer_id": "CUST-000001",
            "address": "123 Test St", "postal_code": "97201", "lat": 45.51, "lon": -122.68,
            "rate_class": "RES", "medical_baseline": False, "critical_facility": False,
            "feeder_id": "FEEDER-COASTAL-01", "account_status": "active",
        },
        "erp_assets": {
            "as_of_utc": now, "asset_id": "WIND-COASTAL-01", "asset_type": "wind_turbine",
            "name": "Coastal Wind 01", "nameplate_mw": 3.0, "voltage_kv": 34.5,
            "region_id": "REGION-COASTAL", "substation_id": "SUB-01", "feeder_id": "FEEDER-COASTAL-01",
            "lat": 45.6, "lon": -123.9, "install_year": 2019, "last_maintenance": now,
            "next_maintenance": now, "status": "active", "oem": "TestOEM",
        },
        "weather_observations": {
            "event_time_utc": now, "region_id": "REGION-COASTAL", "temp_c": 14.5,
            "wind_speed_ms": 9.5, "wind_dir_deg": 250.0, "ghi_wm2": 300.0,
            "cloud_cover_pct": 20.0, "precip_mm_h": 0.0, "alert": None,
        },
        "predictions": {
            "event_time_utc": now, "horizon_minutes": 240, "scope_type": "grid", "scope_id": "GRID-NW",
            "demand_mw_p50": 900.0, "generation_mw_p50": 780.0, "deficit_mw": 120.0,
            "deficit_pct": 13.3, "reserve_margin_pct": 8.0, "spike_probability": 0.4,
            "model_version": "test-v1",
        },
        "activator_alerts": {
            "alert_id": "test-alert-1", "event_time_utc": now, "rule_id": "RULE-TEST",
            "severity": "info", "scope_type": "grid", "scope_id": "GRID-NW", "metric": "test_metric",
            "value": 1.0, "threshold": 1.0, "message": "connectivity test alert",
            "recommended_action": "none (test)", "human_approval_required": True, "status": "open",
        },
    }
    return samples.get(stream, {
        "event_id": "test-evt-1", "event_time_utc": now, "note": f"generic connectivity test for {stream}",
    })


def main():
    parser = argparse.ArgumentParser(description="Test connectivity to one Fabric Eventstream")
    parser.add_argument("stream", nargs="?", choices=KNOWN_STREAMS,
                         help="stream name; reads its FABRIC_ES_<NAME> env var")
    parser.add_argument("--conn-str", help="connection string to use directly instead of an env var")
    args = parser.parse_args()

    if not args.stream and not args.conn_str:
        parser.error("provide a stream name (reads its env var) or --conn-str")

    stream = args.stream or "generation_telemetry"
    conn_str = args.conn_str or os.environ.get(_env_var_for_stream(stream))
    if not conn_str:
        print(f"No connection string found. Set {_env_var_for_stream(stream)} or pass --conn-str.")
        sys.exit(1)

    try:
        from azure.eventhub import EventHubProducerClient, EventData
    except ImportError:
        print("azure-eventhub not installed. Run: pip install -r requirements-fabric.txt")
        sys.exit(1)

    event = build_sample_event(stream)
    print(f"Sending 1 test event for stream '{stream}':")
    print(json.dumps(event, indent=2))

    producer = EventHubProducerClient.from_connection_string(conn_str)
    try:
        batch = producer.create_batch()
        batch.add(EventData(json.dumps(event)))
        producer.send_batch(batch)
        print("\n[OK] Sent successfully. Check your Eventhouse table with:")
        print(f"   {stream} | take 5")
    except Exception as exc:
        print(f"\n[FAILED] Send failed: {exc}")
        sys.exit(1)
    finally:
        producer.close()


if __name__ == "__main__":
    main()
