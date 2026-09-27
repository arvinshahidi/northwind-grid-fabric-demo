"""
Load model: daily shape by customer class (RES/COM/IND), temperature-sensitive
component, weekend vs weekday, and per-feeder diversity mix.
"""
from __future__ import annotations

import math
from datetime import datetime


def _res_shape(hour: float) -> float:
    # morning bump ~07:00, evening peak ~18:30-20:00
    morning = 0.35 * math.exp(-((hour - 7.0) ** 2) / (2 * 1.3 ** 2))
    evening = 1.0 * math.exp(-((hour - 19.0) ** 2) / (2 * 1.8 ** 2))
    base = 0.35
    return base + morning + evening


def _com_shape(hour: float) -> float:
    # daytime plateau 08:00-18:00
    if 7.5 <= hour <= 18.5:
        return 0.9 + 0.1 * math.sin((hour - 7.5) / 11 * math.pi)
    return 0.35


def _ind_shape(hour: float) -> float:
    # flatter, with shift changes at 06:00, 14:00, 22:00
    shift_bumps = sum(0.12 * math.exp(-((hour - s) ** 2) / (2 * 0.4 ** 2)) for s in (6, 14, 22))
    return 0.75 + shift_bumps


def class_shape(rate_class: str, hour: float, is_weekend: bool) -> float:
    if rate_class == "RES":
        v = _res_shape(hour)
        if is_weekend:
            v *= 1.05
        return v
    if rate_class == "COM":
        v = _com_shape(hour)
        if is_weekend:
            v *= 0.55
        return v
    if rate_class == "IND":
        v = _ind_shape(hour)
        if is_weekend:
            v *= 0.8
        return v
    return 0.5


def temperature_adjustment(temp_c: float) -> float:
    """CDD/HDD-lite multiplier: AC load above 24C, heating load below 10C."""
    cdd = max(0.0, temp_c - 24.0)
    hdd = max(0.0, 10.0 - temp_c)
    return 1.0 + 0.045 * cdd + 0.03 * hdd


def feeder_base_demand_mw(feeder_capacity_mva: float, mix: dict, hour_local: float,
                           is_weekend: bool, temp_c: float, rng) -> float:
    """Aggregate feeder demand as fraction of a nominal loading target (~55-70% of capacity at peak)."""
    weighted = (
        mix.get("residential_pct", 0.5) * class_shape("RES", hour_local, is_weekend)
        + mix.get("commercial_pct", 0.3) * class_shape("COM", hour_local, is_weekend)
        + mix.get("industrial_pct", 0.2) * class_shape("IND", hour_local, is_weekend)
    )
    weighted *= temperature_adjustment(temp_c)
    target_peak_mw = feeder_capacity_mva * 0.62
    noise = 1.0 + rng.gauss(0, 0.015)
    return max(0.0, target_peak_mw * weighted * noise)
