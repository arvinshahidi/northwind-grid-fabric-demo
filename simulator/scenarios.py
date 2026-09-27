"""
Scenario injectors. Each scenario mutates `World` state (weather, generator
forced status, demand multipliers) so that Activator rules fire with the
exact Microsoft-style wording. All are additive on top of the baseline day.
"""
from __future__ import annotations

SCENARIOS = {
    "S1": "BASELINE_DAY",
    "S2": "EVENING_PEAK_90",
    "S3": "DEFICIT_15",
    "S4": "WIND_FARM_TRIP",
    "S5": "STORM_TRACK",
    "S6": "SOLAR_RAMP_CLOUD",
    "S7": "DEMAND_RESPONSE",
}

NARRATIVE_CARDS = {
    "S1": (
        "BASELINE DAY - Calm midweek morning ramp. Reserve margins are healthy across all "
        "three regions, wind is moderate at the coast, and the dashboard is all green. This "
        "is our warm-up state before we inject any stress."
    ),
    "S2": (
        "EVENING PEAK 90% - As the evening peak builds between 5 and 8pm, demand climbs "
        "past 90% of available capacity. Activator immediately fires the exact Microsoft rule: "
        "'Demand exceeds 90% of available capacity.' The recommended actions are to start the "
        "gas peakers, discharge the battery, and call a commercial demand-response event -- all "
        "pending human approval."
    ),
    "S3": (
        "DEFICIT 15% - We layer a coastal wind lull on top of the evening peak. The 4-hour "
        "forecast model now shows roughly a 15% supply shortfall during tonight's peak window. "
        "Activator fires 'Forecast 15% supply shortfall during peak evening hours' as a proactive, "
        "not reactive, warning -- giving operators hours of lead time instead of minutes."
    ),
    "S4": (
        "WIND FARM TRIP - Two turbines at our Coastal wind farm trip simultaneously. You'll see "
        "the frequency proxy dip and nearby feeder voltages move. The system recommends redispatch "
        "from peakers and hydro -- it does NOT auto-trip any customers."
    ),
    "S5": (
        "STORM TRACK - A weather front is tracked moving from Coastal into Metro. As it crosses, "
        "wind and solar output collapse and two feeders show correlated last-gasp outages. Field "
        "crews are recommended for dispatch, and an emergency load-shed recommendation only appears "
        "if frequency or reserve margin crosses the hard emergency threshold."
    ),
    "S6": (
        "SOLAR RAMP / CLOUD - A fast-moving cloud field crosses the solar farms and output drops "
        "about 40% in just 10 simulated minutes. This is the classic renewable ramp-risk story "
        "Activator is built to catch before it becomes a deficit."
    ),
    "S7": (
        "DEMAND RESPONSE ACCEPTED - The operator approves a recommended demand-response event. "
        "Commercial load drops 5-8% over 10 minutes, reserve margin recovers, and the alert clears "
        "-- showing the full recommend -> approve -> recover loop without ever touching a breaker."
    ),
}


class ScenarioController:
    def __init__(self, scenario_id: str, world):
        self.scenario_id = scenario_id
        self.world = world
        self.injected = False
        self.inject_at_minute = 0.0 if scenario_id in ("S1",) else 2.0

    def maybe_inject(self, minute_of_run: float):
        if self.injected or minute_of_run < self.inject_at_minute:
            return
        self.injected = True
        fn = getattr(self, f"_inject_{self.scenario_id.lower()}", None)
        if fn:
            fn()

    def tick(self, minute_of_run: float):
        fn = getattr(self, f"_tick_{self.scenario_id.lower()}", None)
        if fn:
            fn(minute_of_run)

    # --- S1 baseline: no-op ---

    # --- S2 evening peak 90% ---
    def _inject_s2(self):
        self.world.demand_boost_target = 1.32
        # take two peakers offline so available capacity is tighter
        offline = [g for g in self.world.gen_states.values() if g.asset_type == "gas_peaker"][:2]
        for g in offline:
            g.status = "offline"
            g.forced_outage = True

    def _tick_s2(self, minute_of_run: float):
        ramp_minutes = 20.0
        prog = min(1.0, (minute_of_run - self.inject_at_minute) / ramp_minutes)
        self.world.demand_boost = 1.0 + (self.world.demand_boost_target - 1.0) * prog

    # --- S3 deficit 15% (peak + wind lull) ---
    def _inject_s3(self):
        self.world.demand_boost_target = 1.32
        # deeper firm-capacity tightening than S2: 3 peakers down + reservoir-limited hydro
        offline = [g for g in self.world.gen_states.values() if g.asset_type == "gas_peaker"][:2]
        for g in offline:
            g.status = "offline"
            g.forced_outage = True
        for g in self.world.gen_states.values():
            if g.asset_type == "hydro":
                g.reservoir_frac = min(g.reservoir_frac, 0.28)  # drought-constrained reservoir
        self.world.weather.state.base_wind_ms = 2.0  # collapse coastal wind well below cut-in

    def _tick_s3(self, minute_of_run: float):
        self._tick_s2(minute_of_run)

    # --- S4 wind farm trip ---
    def _inject_s4(self):
        coastal_turbines = [g for g in self.world.gen_states.values()
                             if g.asset_type == "wind_turbine"]
        coastal_ids = {aid for aid, meta in self.world.gen_meta.items()
                        if meta["region_id"] == "REG-COASTAL" and meta["asset_type"] == "wind_turbine"}
        tripped = 0
        for g in coastal_turbines:
            if g.asset_id in coastal_ids and tripped < 2:
                g.status = "offline"
                g.forced_outage = True
                tripped += 1

    # --- S5 storm track ---
    def _inject_s5(self):
        self.world.weather.start_storm()
        feeders = list(self.world.feeders.values())
        coastal = [f for f in feeders if f["region_id"] == "REG-COASTAL"]
        metro = [f for f in feeders if f["region_id"] == "REG-METRO"]
        self.world.storm_target_feeders = [coastal[0]["feeder_id"], metro[0]["feeder_id"]]

    def _tick_s5(self, minute_of_run: float):
        if self.world.weather.state.storm_active and self.world.weather.state.storm_progress > 0.4:
            self.world.storm_outage_active = True

    # --- S6 solar ramp / cloud ---
    def _inject_s6(self):
        self.world.cloud_ramp_active = True
        self.world.cloud_ramp_start_minute = self.inject_at_minute

    def _tick_s6(self, minute_of_run: float):
        if not self.world.cloud_ramp_active:
            return
        elapsed = minute_of_run - self.world.cloud_ramp_start_minute
        prog = min(1.0, elapsed / 10.0)
        self.world.cloud_ramp_factor = prog  # used by engine to depress GHI up to 40%

    # --- S7 demand response accepted ---
    def _inject_s7(self):
        self.world.dr_active = True
        self.world.dr_start_minute = self.inject_at_minute

    def _tick_s7(self, minute_of_run: float):
        if not self.world.dr_active:
            return
        elapsed = minute_of_run - self.world.dr_start_minute
        prog = min(1.0, elapsed / 10.0)
        self.world.dr_reduction_pct = 0.065 * prog
