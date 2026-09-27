"""
Generation dispatch: power curves + simple unit-commitment state machine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Wind power curve parameters (generic utility turbine)
CUT_IN_MS = 3.0
RATED_MS = 12.0
CUT_OUT_MS = 25.0

GAS_STARTUP_TICKS = 90  # 90 ticks * 5s = 7.5 simulated minutes


def wind_power_mw(nameplate_mw: float, wind_speed_ms: float, correlated_noise: float = 0.0) -> float:
    if wind_speed_ms < CUT_IN_MS or wind_speed_ms >= CUT_OUT_MS:
        return 0.0
    if wind_speed_ms >= RATED_MS:
        frac = 1.0
    else:
        # cubic power curve between cut-in and rated
        frac = ((wind_speed_ms - CUT_IN_MS) / (RATED_MS - CUT_IN_MS)) ** 3
    frac = max(0.0, min(1.0, frac + correlated_noise))
    return round(nameplate_mw * frac, 3)


def solar_power_mw(nameplate_mw: float, ghi_wm2: float) -> float:
    if ghi_wm2 <= 0:
        return 0.0
    frac = min(1.0, ghi_wm2 / 950.0)
    return round(nameplate_mw * frac, 3)


@dataclass
class GenUnitState:
    asset_id: str
    asset_type: str
    nameplate_mw: float
    status: str = "running"  # running | derated | offline | starting
    startup_ticks_remaining: int = 0
    soc_pct: float = 55.0  # BESS only
    reservoir_frac: float = 0.7  # hydro only
    setpoint_mw: float = 0.0
    forced_outage: bool = False

    def request_start(self):
        if self.status == "offline":
            self.status = "starting"
            self.startup_ticks_remaining = GAS_STARTUP_TICKS

    def tick_startup(self):
        if self.status == "starting":
            self.startup_ticks_remaining -= 1
            if self.startup_ticks_remaining <= 0:
                self.status = "running"


def dispatch_hydro(unit: GenUnitState, target_frac: float) -> float:
    if unit.status == "offline" or unit.reservoir_frac <= 0.05:
        return 0.0
    frac = max(0.0, min(1.0, target_frac))
    # slow dispatch: reservoir depletes slowly with output
    output = unit.nameplate_mw * frac * min(1.0, unit.reservoir_frac / 0.3)
    unit.reservoir_frac = max(0.0, unit.reservoir_frac - output / unit.nameplate_mw * 0.0006)
    return round(output, 3)


def dispatch_gas_peaker(unit: GenUnitState, target_frac: float) -> float:
    unit.tick_startup()
    if unit.status in ("offline", "starting"):
        return 0.0
    frac = max(0.0, min(1.0, target_frac))
    return round(unit.nameplate_mw * frac, 3)


def dispatch_bess(unit: GenUnitState, reserve_margin_pct: float, surplus_mw: float) -> float:
    """Positive = discharge (helps supply), negative = charge (draws from supply)."""
    if reserve_margin_pct < 12.0 and unit.soc_pct > 5.0:
        discharge = min(unit.nameplate_mw, unit.soc_pct / 100 * unit.nameplate_mw * 4)
        unit.soc_pct = max(0.0, unit.soc_pct - discharge / unit.nameplate_mw * 1.2)
        return round(discharge, 3)
    if surplus_mw > 5 and unit.soc_pct < 95.0:
        charge = min(unit.nameplate_mw * 0.5, surplus_mw * 0.3)
        unit.soc_pct = min(100.0, unit.soc_pct + charge / unit.nameplate_mw * 1.2)
        return round(-charge, 3)
    return 0.0
