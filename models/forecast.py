"""
Phase 3 - lightweight, explainable forecast models (no deep learning):
  a) energy deficiency prediction (1h / 4h horizons)
  b) demand spike forecast (evening peak)
  c) renewable generation forecast (short-horizon persistence + trend)
  d) grid stability score (reserve margin + frequency + voltage violations)

These are heuristic/statistical, deliberately simple and explainable so a
presenter can describe the "model" in one sentence.
"""
from __future__ import annotations

import math
from simulator.load import class_shape, temperature_adjustment
from simulator.weather import _solar_zenith_factor


def forecast_deficiency(grid_summary_history: list[dict], feeders: dict, local_hour_now: float,
                         is_weekend: bool, avg_temp_c: float, horizon_minutes: int,
                         demand_boost: float = 1.0) -> dict:
    """Project demand using the same deterministic daily shape used by the simulator.
    Firm dispatchable capacity (peaker/hydro/biomass/bess nameplate, minus forced
    outages) is treated as fully available within the horizon. Wind is decayed
    persistence (uncertain further out); solar is projected on the same clear-sky
    curve the simulator itself uses, so it correctly goes to ~0 after sunset
    instead of being over-credited from a sunny "now"."""
    if not grid_summary_history:
        return {"demand_mw_p50": 0, "generation_mw_p50": 0, "deficit_mw": 0, "deficit_pct": 0,
                "reserve_margin_pct": 100}

    latest = grid_summary_history[-1]
    future_hour = (local_hour_now + horizon_minutes / 60.0) % 24

    # Reconstruct an aggregate "shape ratio" between now and future using the
    # blended class shape across all feeders (weighted by capacity).
    total_cap = sum(f["capacity_mva"] for f in feeders.values()) or 1
    def blended_shape(hour):
        s = 0.0
        for f in feeders.values():
            w = f["capacity_mva"] / total_cap
            mix = f["mix"]
            s += w * (
                mix.get("residential_pct", 0.5) * class_shape("RES", hour, is_weekend)
                + mix.get("commercial_pct", 0.3) * class_shape("COM", hour, is_weekend)
                + mix.get("industrial_pct", 0.2) * class_shape("IND", hour, is_weekend)
            )
        return s * temperature_adjustment(avg_temp_c)

    now_shape = blended_shape(local_hour_now) or 1e-6
    future_shape = blended_shape(future_hour)
    demand_ratio = future_shape / now_shape
    demand_mw_p50 = latest["total_demand_mw"] * demand_ratio

    # generation: firm dispatchable fleet is assumed startable within the horizon
    # (peakers/hydro/bess), so we count its full nameplate as available.
    firm_available_mw = latest.get("firm_available_mw", latest["available_capacity_mw"])

    wind_output_mw = latest.get("wind_output_mw", latest.get("renewable_output_mw", 0.0))
    wind_decay = max(0.55, 1 - 0.09 * (horizon_minutes / 60.0))
    wind_mw_p50 = wind_output_mw * wind_decay

    solar_nameplate_mw = latest.get("solar_nameplate_mw", 0.0)
    solar_now_frac = _solar_zenith_factor(local_hour_now)
    solar_future_frac = _solar_zenith_factor(future_hour)
    # scale today's observed solar output by the ratio of future/now clear-sky
    # factor so a sunny "now" doesn't get over-credited onto a dark "future".
    solar_output_mw = latest.get("solar_output_mw", 0.0)
    if solar_now_frac > 0.05:
        solar_mw_p50 = solar_output_mw * (solar_future_frac / solar_now_frac)
    else:
        solar_mw_p50 = solar_nameplate_mw * solar_future_frac * 0.7  # cloud-discounted clear-sky estimate
    solar_mw_p50 = max(0.0, min(solar_nameplate_mw, solar_mw_p50))

    generation_mw_p50 = firm_available_mw + wind_mw_p50 + solar_mw_p50

    deficit_mw = max(0.0, demand_mw_p50 - generation_mw_p50)
    deficit_pct = (deficit_mw / demand_mw_p50 * 100) if demand_mw_p50 else 0.0
    reserve_margin_pct = ((generation_mw_p50 - demand_mw_p50) / demand_mw_p50 * 100
                          ) if demand_mw_p50 else 100.0

    return {
        "demand_mw_p50": round(demand_mw_p50, 2),
        "generation_mw_p50": round(generation_mw_p50, 2),
        "deficit_mw": round(deficit_mw, 2),
        "deficit_pct": round(deficit_pct, 2),
        "reserve_margin_pct": round(reserve_margin_pct, 2),
    }


def demand_spike_probability(local_hour_now: float, horizon_minutes: int) -> float:
    """Evening peak window is 17:00-20:00; probability rises as horizon lands inside it."""
    future_hour = (local_hour_now + horizon_minutes / 60.0) % 24
    if 17.0 <= future_hour <= 20.0:
        centered = 1 - abs(future_hour - 18.5) / 1.5
        return round(min(0.95, max(0.3, 0.5 + 0.45 * centered)), 2)
    return round(max(0.03, 0.15 - abs(future_hour - 18.5) * 0.01), 2)


def renewable_forecast(wind_history: dict, horizon_minutes: int) -> dict:
    """Linear trend extrapolation per wind farm over the last few observations."""
    out = {}
    for farm_id, points in wind_history.items():
        if len(points) < 2:
            out[farm_id] = points[-1][1] if points else 0.0
            continue
        recent = points[-6:]
        xs = [p[0] for p in recent]
        ys = [p[1] for p in recent]
        n = len(xs)
        mean_x = sum(xs) / n
        mean_y = sum(ys) / n
        denom = sum((x - mean_x) ** 2 for x in xs) or 1e-6
        slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denom
        projected = ys[-1] + slope * horizon_minutes
        out[farm_id] = round(max(0.0, projected), 2)
    return out


def wind_drop_pct(wind_history: dict, farm_id: str, farm_nameplate_mw: float = 0.0,
                   lookback_minutes: float = 15.0) -> float:
    """Sustained drop: average of the most recent points vs a baseline window
    ~lookback_minutes ago. Ignored when baseline output is too small (noise)."""
    points = wind_history.get(farm_id, [])
    if len(points) < 4:
        return 0.0
    now_minute = points[-1][0]
    recent = [mw for m, mw in points[-8:]]
    now_avg = sum(recent) / len(recent)
    baseline_window = [mw for m, mw in points if lookback_minutes - 2 <= now_minute - m <= lookback_minutes + 2]
    if not baseline_window:
        baseline_window = [points[0][1]]
    baseline = sum(baseline_window) / len(baseline_window)
    min_meaningful = max(1.5, 0.35 * farm_nameplate_mw) if farm_nameplate_mw else 1.5
    if baseline < min_meaningful:
        return 0.0
    return round(max(0.0, (baseline - now_avg) / baseline * 100), 1)


def grid_stability_score(reserve_margin_pct: float, frequency_hz: float,
                          voltage_violation_count: int, nominal_hz: float = 60.0) -> float:
    """0-100 explainable composite: reserve margin (weight 50), frequency
    deviation (weight 30), voltage violations (weight 20)."""
    margin_score = max(0.0, min(1.0, reserve_margin_pct / 25.0)) * 50
    freq_dev = abs(frequency_hz - nominal_hz)
    freq_score = max(0.0, 1 - freq_dev / 0.5) * 30
    violation_score = max(0.0, 1 - voltage_violation_count / 20.0) * 20
    return round(margin_score + freq_score + violation_score, 1)
