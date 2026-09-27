"""
Simulator CLI - STEP C runnable entry point.

Usage:
  python -m simulator --scenario S1 --minutes 30 --speed 30 --out data/run1
  python -m simulator --scenario S2 --minutes 45 --speed 60 --out data/run_s2 --no-sleep

Writes ndjson per stream + a run_summary.json (peak demand, min reserve,
alerts fired) to --out.
"""
from __future__ import annotations

import argparse
import json
import os
import time

from simulator.engine import World
from simulator.scenarios import NARRATIVE_CARDS, SCENARIOS
from ingest.bus import EventBus, NdjsonSink
from enrich.join import enrich_generation, enrich_meters, build_erp_index, build_crm_index
from models.forecast import (
    forecast_deficiency, demand_spike_probability, renewable_forecast,
    wind_drop_pct, grid_stability_score,
)
from activate.rules import evaluate_rules


def run(scenario_id: str, minutes: float, speed: float, out_dir: str, sleep: bool = False,
        predict_every_ticks: int = 12, start_hour: float | None = None) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    start_local = None
    if start_hour is not None:
        from datetime import datetime, timedelta
        now = datetime.now()
        h = int(start_hour)
        m = int(round((start_hour - h) * 60))
        start_local = now.replace(hour=h, minute=m, second=0, microsecond=0)
        while start_local.weekday() >= 5:
            start_local += timedelta(days=1)
    world = World(scenario_id=scenario_id, speed=speed, start_local=start_local)

    bus = EventBus()
    sink = NdjsonSink(out_dir)
    bus.subscribe(sink)
    try:
        from fabric.connectors.eventstream_publisher import maybe_attach_fabric_sink
        # Backfill mode is OFF unless FABRIC_BACKFILL_MINUTES is set in the
        # environment; run_minutes_hint just lets "auto" resolve to this
        # run's own --minutes value for a clean 1:1 backfill map.
        maybe_attach_fabric_sink(bus, run_minutes_hint=minutes)
    except ImportError:
        pass  # azure-eventhub not installed; local-only demo

    erp_rows = world.erp_snapshot()
    crm_rows = world.crm_snapshot()
    erp_index = build_erp_index(erp_rows)
    crm_index = build_crm_index(crm_rows)
    bus.publish_many("erp_assets", erp_rows)
    bus.publish_many("crm_context", crm_rows)

    total_ticks = int(minutes * 60 / world.clock.tick_seconds)
    peak_demand = 0.0
    min_reserve = 1e9
    alerts_fired: list[dict] = []
    max_demand_pct = 0.0
    max_deficit_pct_4h = 0.0

    print(f"=== STEP C: running scenario {scenario_id} ({SCENARIOS.get(scenario_id, '?')}) ===")
    print(NARRATIVE_CARDS.get(scenario_id, ""))
    print(f"Ticks: {total_ticks}  (tick={world.clock.tick_seconds}s sim, speed={speed}x)")

    for tick_i in range(total_ticks):
        events, grid_summary = world.tick()

        gen_enriched = enrich_generation(events["generation_telemetry"], erp_index)
        meter_enriched = enrich_meters(events["meter_telemetry"], crm_index)

        bus.publish_many("generation_telemetry", gen_enriched)
        bus.publish_many("grid_telemetry", events["grid_telemetry"])
        bus.publish_many("meter_telemetry", meter_enriched)
        bus.publish_many("weather_observations", events["weather_observations"])

        peak_demand = max(peak_demand, grid_summary["total_demand_mw"])
        max_demand_pct = max(max_demand_pct, grid_summary["demand_pct_of_capacity"])
        min_reserve = min(min_reserve, grid_summary["reserve_margin_pct"])

        predictions = []
        if tick_i % predict_every_ticks == 0:
            local_hour = world.clock.local_time.hour + world.clock.local_time.minute / 60.0
            avg_temp = sum(o["temp_c"] for o in events["weather_observations"]) / max(1, len(events["weather_observations"]))
            for horizon in (60, 240):
                d = forecast_deficiency(world.history["grid_summary"], world.feeders, local_hour,
                                         world.is_weekend(), avg_temp, horizon, world.demand_boost)
                spike_p = demand_spike_probability(local_hour, horizon)
                pred = {
                    "event_time_utc": world.clock.iso_utc(), "horizon_minutes": horizon,
                    "scope_type": "grid", "scope_id": "GRID-NW",
                    "demand_mw_p50": d["demand_mw_p50"], "generation_mw_p50": d["generation_mw_p50"],
                    "deficit_mw": d["deficit_mw"], "deficit_pct": d["deficit_pct"],
                    "reserve_margin_pct": d["reserve_margin_pct"], "spike_probability": spike_p,
                    "model_version": "heuristic-v1",
                }
                predictions.append(pred)
                if horizon == 240:
                    max_deficit_pct_4h = max(max_deficit_pct_4h, d["deficit_pct"])
            bus.publish_many("predictions", predictions)

        wind_drops = {farm: wind_drop_pct(world.wind_history, farm, world.farm_nameplate.get(farm, 0.0))
                      for farm in world.wind_history}
        new_alerts = evaluate_rules(grid_summary, predictions, events["grid_telemetry"],
                                     meter_enriched, wind_drops, world.clock.iso_utc(),
                                     alert_state=world.gen_alerts_state, minute_of_run=world.minute_of_run)
        if new_alerts:
            bus.publish_many("activator_alerts", new_alerts)
            alerts_fired.extend(new_alerts)

        if sleep:
            time.sleep(world.clock.wall_sleep_seconds)

    sink.close()

    rule_counts: dict[str, int] = {}
    for a in alerts_fired:
        rule_counts[a["rule_id"]] = rule_counts.get(a["rule_id"], 0) + 1

    summary = {
        "scenario_id": scenario_id, "scenario_name": SCENARIOS.get(scenario_id),
        "minutes_simulated": minutes, "ticks": total_ticks,
        "peak_demand_mw": round(peak_demand, 2), "max_demand_pct_of_capacity": round(max_demand_pct, 2),
        "min_reserve_margin_pct": round(min_reserve, 2),
        "max_4h_forecast_deficit_pct": round(max_deficit_pct_4h, 2),
        "alerts_fired_total": len(alerts_fired), "alerts_by_rule": rule_counts,
        "out_dir": out_dir,
    }
    with open(os.path.join(out_dir, "run_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print("\n--- run summary ---")
    print(json.dumps(summary, indent=2))
    return summary


def main():
    parser = argparse.ArgumentParser(description="Northwind Grid simulator")
    parser.add_argument("--scenario", default="S1", choices=list(SCENARIOS.keys()))
    parser.add_argument("--minutes", type=float, default=30.0)
    parser.add_argument("--speed", type=float, default=60.0)
    parser.add_argument("--out", default=os.path.join("data", "run1"))
    parser.add_argument("--sleep", action="store_true", help="sleep wall-clock between ticks (real-time demo pacing)")
    parser.add_argument("--start-hour", type=float, default=None,
                         help="override local start hour (e.g. 16.5 for 16:30) instead of default 05:00 next weekday")
    args = parser.parse_args()
    run(args.scenario, args.minutes, args.speed, args.out, sleep=args.sleep, start_hour=args.start_hour)


if __name__ == "__main__":
    main()
