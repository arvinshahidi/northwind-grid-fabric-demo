"""
World engine: owns all state for one simulation run and produces one batch
of stream events per tick (Phase 1 raw telemetry). Enrichment (Phase 2),
models (Phase 3) and activator (Phase 4) run on top of the emitted events.
"""
from __future__ import annotations

import json
import os
import random
import uuid
from datetime import timedelta

from simulator.clock import SimClock
from simulator.weather import WeatherEngine, REGION_ORDER
from simulator.load import feeder_base_demand_mw
from simulator.generation import (
    GenUnitState, wind_power_mw, solar_power_mw, dispatch_hydro,
    dispatch_gas_peaker, dispatch_bess,
)
from simulator.scenarios import ScenarioController

SEED = 42
TOPOLOGY_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "data", "seed", "topology.json")


def load_topology(path: str = TOPOLOGY_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class World:
    def __init__(self, scenario_id: str = "S1", start_local=None, speed: float = 60.0,
                 seed: int = SEED, topology_path: str = TOPOLOGY_PATH):
        self.topology = load_topology(topology_path)
        self.rng = random.Random(seed)
        self.weather = WeatherEngine(random.Random(seed + 1))

        from simulator.clock import default_start
        start_local = start_local or default_start()
        self.clock = SimClock.starting_at(start_local, speed=speed)

        self.regions = {r["region_id"]: r for r in self.topology["regions"]}
        self.substations = {s["substation_id"]: s for s in self.topology["substations"]}
        self.feeders = {f["feeder_id"]: f for f in self.topology["feeders"]}
        self.generators = {g["asset_id"]: g for g in self.topology["generators"]}
        self.meters = self.topology["meters"]
        self.crews = self.topology["crews"]

        self.gen_meta = self.generators
        self.gen_states: dict[str, GenUnitState] = {}
        peaker_count = 0
        for g in self.topology["generators"]:
            self.gen_states[g["asset_id"]] = GenUnitState(
                asset_id=g["asset_id"], asset_type=g["asset_type"], nameplate_mw=g["nameplate_mw"],
            )
            if g["asset_type"] == "gas_peaker":
                peaker_count += 1
                # Grid doesn't start "cold" -- assume 1 peaker is already warm/committed
                # to cover typical overnight baseload; the rest are cold-start reserve.
                self.gen_states[g["asset_id"]].status = "running" if peaker_count == 1 else "offline"

        # meters grouped by feeder for fast aggregation
        self.meters_by_feeder: dict[str, list] = {}
        for m in self.meters:
            self.meters_by_feeder.setdefault(m["feeder_id"], []).append(m)

        self.farm_nameplate: dict[str, float] = {}
        for g in self.topology["generators"]:
            if g["asset_type"] == "wind_turbine":
                self.farm_nameplate[g["farm_or_plant_id"]] = self.farm_nameplate.get(g["farm_or_plant_id"], 0.0) + g["nameplate_mw"]

        self.scenario_id = scenario_id
        self.scenario = ScenarioController(scenario_id, self)

        # scenario-controlled flags
        self.demand_boost = 1.0
        self.demand_boost_target = 1.0
        self.storm_target_feeders: list[str] = []
        self.storm_outage_active = False
        self.cloud_ramp_active = False
        self.cloud_ramp_factor = 0.0
        self.dr_active = False
        self.dr_reduction_pct = 0.0

        # rolling telemetry history for enrichment/models (kept short, in-memory)
        self.history: dict[str, list] = {"grid_summary": []}
        self.wind_history: dict[str, list] = {}  # farm_or_plant_id -> [(minute, mw)]

        self.meter_rotation_index = 0
        self.gen_alerts_state: dict = {}

    @property
    def minute_of_run(self) -> float:
        return self.clock.ticks_elapsed * self.clock.tick_seconds / 60.0

    def is_weekend(self) -> bool:
        return self.clock.local_time.weekday() >= 5

    # ---------------------------------------------------------- weather
    def _weather_for_region(self, region_id: str) -> dict:
        obs = self.weather.observe(region_id, self.clock.local_time, self.minute_of_run)
        if self.cloud_ramp_active:
            obs["cloud_cover_pct"] = min(100.0, obs["cloud_cover_pct"] + 55 * self.cloud_ramp_factor)
            obs["ghi_wm2"] = round(obs["ghi_wm2"] * (1 - 0.40 * self.cloud_ramp_factor), 1)
        return obs

    # ---------------------------------------------------------- one tick
    def tick(self):
        self.scenario.maybe_inject(self.minute_of_run)
        self.scenario.tick(self.minute_of_run)
        self.weather.advance_storm(self.clock.tick_seconds / 60.0)
        self.clock.tick()

        events = {
            "generation_telemetry": [], "grid_telemetry": [], "meter_telemetry": [],
            "weather_observations": [],
        }

        weather_by_region = {rid: self._weather_for_region(rid) for rid in REGION_ORDER}
        for rid, obs in weather_by_region.items():
            events["weather_observations"].append({
                "event_time_utc": self.clock.iso_utc(), **obs,
            })

        # ---- demand first: feeder loads only depend on weather/time, not generation ----
        total_demand = 0.0
        feeder_loads: dict[str, float] = {}
        for feeder_id, feeder in self.feeders.items():
            temp = weather_by_region[feeder["region_id"]]["temp_c"]
            mw = feeder_base_demand_mw(
                feeder["capacity_mva"], feeder["mix"], self.clock.local_time.hour + self.clock.local_time.minute / 60.0,
                self.is_weekend(), temp, self.rng,
            )
            mw *= self.demand_boost
            if self.dr_active and feeder["mix"].get("commercial_pct", 0) > 0.2:
                mw *= (1 - self.dr_reduction_pct)
            feeder_loads[feeder_id] = mw
            total_demand += mw

        # ---- generation dispatch: weather-driven resources first, then firm
        # units (gas peakers + BESS) are dispatched in merit order to close the
        # gap to demand -- this is what economic dispatch/AGC does in reality,
        # and keeps the frequency proxy meaningful instead of drifting for no
        # physical reason. ----
        gen_by_feeder: dict[str, float] = {}
        gen_by_region: dict[str, float] = {}
        total_generation = 0.0
        wind_now: dict[str, float] = {}
        peaker_setpoints: dict[str, float] = {}
        bess_setpoints: dict[str, float] = {}

        base_output = 0.0  # wind + solar + hydro + biomass
        for asset_id, gmeta in self.generators.items():
            state = self.gen_states[asset_id]
            if gmeta["asset_type"] not in ("wind_turbine", "solar_farm", "hydro", "biomass"):
                continue
            obs = weather_by_region[gmeta["region_id"]]
            mw = 0.0
            if gmeta["asset_type"] == "wind_turbine" and not state.forced_outage:
                corr_noise = self.rng.gauss(0, 0.03)
                mw = wind_power_mw(gmeta["nameplate_mw"], obs["wind_speed_ms"], corr_noise)
                state.status = "running" if mw > 0 else "derated"
            elif gmeta["asset_type"] == "solar_farm" and not state.forced_outage:
                mw = solar_power_mw(gmeta["nameplate_mw"], obs["ghi_wm2"])
                state.status = "running" if mw > 0 else "derated"
            elif gmeta["asset_type"] == "hydro" and not state.forced_outage:
                mw = dispatch_hydro(state, target_frac=0.65)
            elif gmeta["asset_type"] == "biomass" and not state.forced_outage:
                mw = gmeta["nameplate_mw"] * 0.85
                state.status = "running"
            if state.forced_outage:
                state.status = "offline"
                mw = 0.0
            state.setpoint_mw = mw
            base_output += mw
            if gmeta["asset_type"] == "wind_turbine":
                wind_now[gmeta["farm_or_plant_id"]] = wind_now.get(gmeta["farm_or_plant_id"], 0.0) + mw

        # residual gap to be met by dispatchable peakers, then BESS
        gap_mw = total_demand - base_output
        prev_summary = self.history["grid_summary"][-1] if self.history["grid_summary"] else None
        reserve_margin_est = prev_summary["reserve_margin_pct"] if prev_summary else 20.0

        peakers = [g for aid, g in self.gen_states.items() if g.asset_type == "gas_peaker"]
        online_peaker_capacity = sum(g.nameplate_mw for g in peakers if g.status == "running" and not g.forced_outage)
        if gap_mw > online_peaker_capacity * 0.85 or reserve_margin_est < 15.0:
            for g in peakers:
                if g.status == "offline" and not g.forced_outage:
                    g.request_start()
        remaining_gap = gap_mw
        for g in peakers:
            g.tick_startup()
            if g.forced_outage or g.status in ("offline", "starting"):
                peaker_setpoints[g.asset_id] = 0.0
                continue
            take = max(0.0, min(g.nameplate_mw, remaining_gap))
            peaker_setpoints[g.asset_id] = round(take, 3)
            remaining_gap -= take
            g.setpoint_mw = take

        surplus_after_peakers = -remaining_gap  # positive = surplus, negative = still short
        bess_units = [g for aid, g in self.gen_states.items() if g.asset_type == "bess" and not g.forced_outage]
        for g in bess_units:
            mw = dispatch_bess(g, reserve_margin_est, max(0.0, surplus_after_peakers))
            bess_setpoints[g.asset_id] = mw
            g.setpoint_mw = mw

        for asset_id, gmeta in self.generators.items():
            state = self.gen_states[asset_id]
            if gmeta["asset_type"] == "gas_peaker":
                mw = peaker_setpoints.get(asset_id, 0.0)
            elif gmeta["asset_type"] == "bess":
                mw = bess_setpoints.get(asset_id, 0.0)
            else:
                mw = state.setpoint_mw
            if state.forced_outage:
                state.status = "offline"
                mw = 0.0
            total_generation += mw
            gen_by_feeder[gmeta["feeder_id"]] = gen_by_feeder.get(gmeta["feeder_id"], 0.0) + mw
            gen_by_region[gmeta["region_id"]] = gen_by_region.get(gmeta["region_id"], 0.0) + mw

            obs = weather_by_region[gmeta["region_id"]]
            events["generation_telemetry"].append({
                "event_id": str(uuid.uuid4()), "event_time_utc": self.clock.iso_utc(),
                "event_time_local": self.clock.iso_local(),
                "asset_id": asset_id, "asset_type": gmeta["asset_type"],
                "farm_or_plant_id": gmeta["farm_or_plant_id"], "region_id": gmeta["region_id"],
                "substation_id": gmeta["substation_id"],
                "active_power_mw": round(mw, 3),
                "reactive_power_mvar": round(mw * 0.12, 3),
                "voltage_kv": gmeta["voltage_kv"] * (1 + self.rng.gauss(0, 0.002)),
                "frequency_hz": 60.0,  # filled in after grid balance below
                "availability_pct": 0.0 if state.status == "offline" else 97.5,
                "status": state.status,
                "wind_speed_ms": obs["wind_speed_ms"] if gmeta["asset_type"] == "wind_turbine" else None,
                "irradiance_wm2": obs["ghi_wm2"] if gmeta["asset_type"] == "solar_farm" else None,
                "soc_pct": round(state.soc_pct, 1) if gmeta["asset_type"] == "bess" else None,
                "curtailment_mw": 0.0,
                "setpoint_mw": round(mw, 3),
            })

        for farm_id, mw in wind_now.items():
            hist = self.wind_history.setdefault(farm_id, [])
            hist.append((self.minute_of_run, mw))
            if len(hist) > 60:
                del hist[0]

        total_available_capacity = self._available_capacity()
        firm_available_mw = self._firm_available_capacity()
        wind_output_mw = sum(s.setpoint_mw for aid, s in self.gen_states.items()
                              if self.generators[aid]["asset_type"] == "wind_turbine")
        solar_output_mw = sum(s.setpoint_mw for aid, s in self.gen_states.items()
                               if self.generators[aid]["asset_type"] == "solar_farm")
        solar_nameplate_mw = sum(g["nameplate_mw"] for g in self.generators.values() if g["asset_type"] == "solar_farm")
        renewable_output_mw = wind_output_mw + solar_output_mw
        demand_pct_of_capacity = (total_demand / total_available_capacity * 100) if total_available_capacity else 0
        surplus_mw = total_generation - total_demand
        reserve_margin_pct = ((total_available_capacity - total_demand) / total_demand * 100) if total_demand else 100
        freq_deviation = max(-0.6, min(0.6, surplus_mw / max(total_demand, 1) * 3.0))
        frequency_hz = round(60.0 + freq_deviation, 3)
        for gt in events["generation_telemetry"]:
            gt["frequency_hz"] = frequency_hz

        substation_load: dict[str, float] = {}
        substation_gen: dict[str, float] = {}
        for feeder_id, feeder in self.feeders.items():
            load_mw = feeder_loads[feeder_id]
            gen_mw = gen_by_feeder.get(feeder_id, 0.0)
            loading_pct = (load_mw / feeder["capacity_mva"] * 100) if feeder["capacity_mva"] else 0
            outage = self.storm_outage_active and feeder_id in self.storm_target_feeders
            events["grid_telemetry"].append({
                "event_id": str(uuid.uuid4()), "event_time_utc": self.clock.iso_utc(),
                "event_time_local": self.clock.iso_local(),
                "element_id": feeder_id, "element_type": "feeder", "region_id": feeder["region_id"],
                "load_mw": round(0.0 if outage else load_mw, 3), "generation_in_mw": round(gen_mw, 3),
                "loading_pct": round(0.0 if outage else loading_pct, 2),
                "voltage_kv": round(feeder["voltage_kv"] * (0.0 if outage else 1.0), 2),
                "frequency_hz": frequency_hz, "unbalance_pct": round(abs(self.rng.gauss(1.0, 0.3)), 2),
                "status": "outage" if outage else ("overload" if loading_pct > 90 else "normal"),
            })
            substation_load[feeder["substation_id"]] = substation_load.get(feeder["substation_id"], 0.0) + (0 if outage else load_mw)
            substation_gen[feeder["substation_id"]] = substation_gen.get(feeder["substation_id"], 0.0) + gen_mw

        for sub_id, sub in self.substations.items():
            load_mw = substation_load.get(sub_id, 0.0)
            gen_mw = substation_gen.get(sub_id, 0.0)
            cap = sum(f["capacity_mva"] for f in self.feeders.values() if f["substation_id"] == sub_id)
            loading_pct = (load_mw / cap * 100) if cap else 0
            events["grid_telemetry"].append({
                "event_id": str(uuid.uuid4()), "event_time_utc": self.clock.iso_utc(),
                "event_time_local": self.clock.iso_local(),
                "element_id": sub_id, "element_type": "substation", "region_id": sub["region_id"],
                "load_mw": round(load_mw, 3), "generation_in_mw": round(gen_mw, 3),
                "loading_pct": round(loading_pct, 2), "voltage_kv": sub["voltage_kv"],
                "frequency_hz": frequency_hz, "unbalance_pct": round(abs(self.rng.gauss(0.8, 0.2)), 2),
                "status": "overload" if loading_pct > 90 else "normal",
            })

        # ---- meter telemetry: rotate through ~1/12th of meters per tick ----
        n_meters = len(self.meters)
        slice_size = max(1, n_meters // 12)
        start = (self.meter_rotation_index * slice_size) % n_meters
        idxs = [ (start + i) % n_meters for i in range(slice_size) ]
        self.meter_rotation_index += 1
        for i in idxs:
            m = self.meters[i]
            feeder = self.feeders[m["feeder_id"]]
            outage = self.storm_outage_active and m["feeder_id"] in self.storm_target_feeders
            per_meter_kw = (feeder_loads[m["feeder_id"]] * 1000) / max(1, len(self.meters_by_feeder[m["feeder_id"]]))
            per_meter_kw *= (0.6 + 1.0 * self.rng.random() * 0.8) if m["rate_class"] == "IND" else 1.0
            voltage_v = round((0.0 if outage else 240.0) + (0 if outage else self.rng.gauss(0, 1.5)), 2)
            events["meter_telemetry"].append({
                "event_id": str(uuid.uuid4()), "event_time_utc": self.clock.iso_utc(),
                "event_time_local": self.clock.iso_local(),
                "meter_id": m["meter_id"], "feeder_id": m["feeder_id"], "substation_id": m["substation_id"],
                "region_id": m["region_id"],
                "active_power_kw": round(0.0 if outage else per_meter_kw, 3),
                "voltage_v": voltage_v,
                "current_a": round(0.0 if outage else per_meter_kw * 1000 / 240 / 3, 2),
                "power_factor": round(self.rng.uniform(0.93, 0.99), 3),
                "energy_kwh_delta": round((0.0 if outage else per_meter_kw) * (self.clock.tick_seconds / 3600), 5),
                "voltage_violation": bool(voltage_v < 216 or voltage_v > 254),
                "last_gasp": bool(outage),
                "restoration": False,
                "quality_flag": "good",
            })

        grid_summary = {
            "event_time_utc": self.clock.iso_utc(), "event_time_local": self.clock.iso_local(),
            "total_demand_mw": round(total_demand, 2), "total_generation_mw": round(total_generation, 2),
            "available_capacity_mw": round(total_available_capacity, 2),
            "firm_available_mw": round(firm_available_mw, 2),
            "renewable_output_mw": round(renewable_output_mw, 2),
            "wind_output_mw": round(wind_output_mw, 2),
            "solar_output_mw": round(solar_output_mw, 2),
            "solar_nameplate_mw": round(solar_nameplate_mw, 2),
            "demand_pct_of_capacity": round(demand_pct_of_capacity, 2),
            "reserve_margin_pct": round(reserve_margin_pct, 2), "frequency_hz": frequency_hz,
            "surplus_mw": round(surplus_mw, 2),
            "gen_by_region": {k: round(v, 2) for k, v in gen_by_region.items()},
            "demand_by_region": self._demand_by_region(feeder_loads),
            "minute_of_run": self.minute_of_run,
        }
        self.history["grid_summary"].append(grid_summary)
        if len(self.history["grid_summary"]) > 720:
            del self.history["grid_summary"][0]

        return events, grid_summary

    def _demand_by_region(self, feeder_loads: dict) -> dict:
        out: dict[str, float] = {}
        for feeder_id, mw in feeder_loads.items():
            rid = self.feeders[feeder_id]["region_id"]
            out[rid] = out.get(rid, 0.0) + mw
        return {k: round(v, 2) for k, v in out.items()}

    def _available_capacity(self) -> float:
        total = 0.0
        for asset_id, gmeta in self.generators.items():
            state = self.gen_states[asset_id]
            if state.forced_outage:
                continue
            if gmeta["asset_type"] in ("wind_turbine", "solar_farm"):
                continue  # variable resources aren't "firm" available capacity
            total += gmeta["nameplate_mw"]
        # add a renewables capacity credit (typical demo simplification)
        renewable_nameplate = sum(g["nameplate_mw"] for g in self.generators.values()
                                   if g["asset_type"] in ("wind_turbine", "solar_farm"))
        total += renewable_nameplate * 0.25
        return total

    def _firm_available_capacity(self) -> float:
        """Dispatchable nameplate (hydro/biomass/gas/bess) excluding forced outages.
        Hydro is derated by reservoir state, mirroring dispatch_hydro's constraint."""
        total = 0.0
        for aid, g in self.generators.items():
            state = self.gen_states[aid]
            if g["asset_type"] in ("wind_turbine", "solar_farm") or state.forced_outage:
                continue
            if g["asset_type"] == "hydro":
                total += g["nameplate_mw"] * min(1.0, state.reservoir_frac / 0.3)
            else:
                total += g["nameplate_mw"]
        return total

    def crm_snapshot(self):
        out = []
        for m in self.meters:
            out.append({
                "event_time_utc": self.clock.iso_utc(), "meter_id": m["meter_id"],
                "customer_id": m["meter_id"].replace("MTR", "CUST"),
                "address": "", "postal_code": "", "lat": 0.0, "lon": 0.0,
                "rate_class": m["rate_class"], "medical_baseline": m["medical_baseline"],
                "critical_facility": m["critical_facility"], "feeder_id": m["feeder_id"],
                "account_status": "active",
            })
        return out

    def erp_snapshot(self):
        out = []
        for g in self.topology["generators"]:
            out.append({**g, "as_of_utc": self.clock.iso_utc()})
        return out
