"""
STEP G - invariant tests. These fail if the synthetic world stops making
physical sense. Run with: pytest -q
"""
from __future__ import annotations

import csv
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from simulator.engine import World
from simulator.generation import wind_power_mw, solar_power_mw, CUT_IN_MS, CUT_OUT_MS, RATED_MS
from models.forecast import wind_drop_pct


DATA_SEED = os.path.join(ROOT, "data", "seed")


def _run_world(scenario_id="S1", ticks=40, start_hour=None):
    world = World(scenario_id=scenario_id, speed=60.0, start_local=None if start_hour is None else _dt(start_hour))
    events_hist = []
    summaries = []
    for _ in range(ticks):
        events, summary = world.tick()
        events_hist.append(events)
        summaries.append(summary)
    return world, events_hist, summaries


def _dt(hour):
    from datetime import datetime, timedelta
    now = datetime.now()
    h = int(hour)
    m = int(round((hour - h) * 60))
    d = now.replace(hour=h, minute=m, second=0, microsecond=0)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


# ---------------------------------------------------------------- topology/masters

def test_every_meter_exists_in_crm_master():
    with open(os.path.join(DATA_SEED, "meters.csv"), encoding="utf-8") as f:
        meter_ids = {row["meter_id"] for row in csv.DictReader(f)}
    with open(os.path.join(DATA_SEED, "customer_master.csv"), encoding="utf-8") as f:
        crm_meter_ids = {row["meter_id"] for row in csv.DictReader(f)}
    assert meter_ids, "no meters generated"
    assert meter_ids == crm_meter_ids


def test_every_asset_exists_in_erp_master():
    with open(os.path.join(DATA_SEED, "generators.csv"), encoding="utf-8") as f:
        asset_ids = {row["asset_id"] for row in csv.DictReader(f)}
    with open(os.path.join(DATA_SEED, "asset_master.csv"), encoding="utf-8") as f:
        erp_ids = {row["asset_id"] for row in csv.DictReader(f)}
    assert asset_ids, "no generators found"
    assert asset_ids == erp_ids


def test_topology_join_path_meter_to_region():
    with open(os.path.join(DATA_SEED, "topology.json"), encoding="utf-8") as f:
        topo = json.load(f)
    feeder_ids = {f["feeder_id"] for f in topo["feeders"]}
    sub_ids = {s["substation_id"] for s in topo["substations"]}
    region_ids = {r["region_id"] for r in topo["regions"]}
    for m in topo["meters"]:
        assert m["feeder_id"] in feeder_ids
        assert m["substation_id"] in sub_ids
        assert m["region_id"] in region_ids


def test_nameplate_totals_within_target():
    with open(os.path.join(DATA_SEED, "generators.csv"), encoding="utf-8") as f:
        total = sum(float(row["nameplate_mw"]) for row in csv.DictReader(f))
    assert 1050 <= total <= 1350, f"total nameplate {total} MW outside 1050-1350 MW target band"


# ---------------------------------------------------------------- physics invariants

def test_solar_zero_at_night():
    assert solar_power_mw(20.0, 0.0) == 0.0
    assert solar_power_mw(20.0, -5.0) == 0.0


def test_solar_positive_in_daylight():
    assert solar_power_mw(20.0, 500.0) > 0.0


def test_wind_zero_below_cut_in_and_above_cut_out():
    assert wind_power_mw(4.0, CUT_IN_MS - 0.5) == 0.0
    assert wind_power_mw(4.0, CUT_OUT_MS + 1.0) == 0.0


def test_wind_full_rated_above_rated_speed():
    assert wind_power_mw(4.0, RATED_MS + 1.0) == pytest.approx(4.0, abs=0.01)


def test_no_future_timestamps_during_replay():
    world, events_hist, summaries = _run_world(ticks=20)
    prev = None
    for s in summaries:
        cur = s["event_time_utc"]
        if prev is not None:
            assert cur >= prev, "simulated clock must be monotonically non-decreasing"
        prev = cur


def test_feeder_meter_energy_diversity_within_tolerance():
    """Sum of sampled meter kW on a feeder, scaled to full feeder population,
    should track the feeder's load_mw within diversity/loss tolerance."""
    world, events_hist, summaries = _run_world(ticks=1)
    last = events_hist[-1]
    grid_by_feeder = {e["element_id"]: e for e in last["grid_telemetry"] if e["element_type"] == "feeder"}
    meters_by_feeder = {}
    for m in last["meter_telemetry"]:
        meters_by_feeder.setdefault(m["feeder_id"], []).append(m)
    checked = 0
    for feeder_id, meters in meters_by_feeder.items():
        if feeder_id not in grid_by_feeder or not meters:
            continue
        sampled_kw = sum(m["active_power_kw"] for m in meters)
        full_population = len(world.meters_by_feeder[feeder_id])
        scaled_mw = sampled_kw / len(meters) * full_population / 1000.0
        feeder_mw = grid_by_feeder[feeder_id]["load_mw"]
        if feeder_mw < 0.5:
            continue
        assert scaled_mw == pytest.approx(feeder_mw, rel=0.05), (
            f"feeder {feeder_id}: scaled meter sum {scaled_mw:.2f} MW vs feeder load {feeder_mw:.2f} MW"
        )
        checked += 1
    assert checked > 0, "no feeders had a meter sample this tick to check"


def test_energy_balance_load_approx_generation():
    """Firm generation is dispatched in merit order to close the gap to demand,
    so generation should track demand closely (residual = losses/rounding)
    once past the initial peaker warm-up window."""
    world, events_hist, summaries = _run_world(ticks=150)
    for s in summaries[-10:]:
        residual_pct = abs(s["total_generation_mw"] - s["total_demand_mw"]) / max(s["total_demand_mw"], 1) * 100
        assert residual_pct < 8.0, f"energy balance residual too large: {residual_pct:.1f}%"


# ---------------------------------------------------------------- scenario invariants

def test_scenario_s2_crosses_90_percent_capacity():
    world, events_hist, summaries = _run_world(scenario_id="S2", ticks=720, start_hour=16.5)
    max_pct = max(s["demand_pct_of_capacity"] for s in summaries)
    assert max_pct > 90.0, f"S2 should cross 90% of available capacity, got max {max_pct:.1f}%"


def test_scenario_s3_produces_15pct_deficit():
    from models.forecast import forecast_deficiency
    world, events_hist, summaries = _run_world(scenario_id="S3", ticks=1080, start_hour=15.0)
    max_deficit = 0.0
    local_hour = 15.0
    for i, s in enumerate(summaries):
        if i % 12 != 0:
            continue
        hour = (local_hour + (i * 5 / 3600.0)) % 24
        d = forecast_deficiency(summaries[: i + 1], world.feeders, hour, False, 16.0, 240, world.demand_boost)
        max_deficit = max(max_deficit, d["deficit_pct"])
    assert 10.0 <= max_deficit <= 20.0, f"S3 4h deficit should be ~15% +/- a few points, got {max_deficit:.1f}%"


def test_scenario_s4_trips_two_coastal_turbines():
    world, events_hist, summaries = _run_world(scenario_id="S4", ticks=30)
    tripped = [g for g in world.gen_states.values() if g.asset_type == "wind_turbine" and g.forced_outage]
    assert len(tripped) == 2
