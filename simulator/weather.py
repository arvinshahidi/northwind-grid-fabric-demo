"""
Weather model: per-region temperature, wind, irradiance, clouds, precipitation.
A wind "front" propagates Coastal -> Metro -> Inland with a time delay so wind
speed changes are spatially correlated, not independent per region.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

REGION_ORDER = ["REG-COASTAL", "REG-METRO", "REG-INLAND"]
FRONT_DELAY_MIN = {"REG-COASTAL": 0, "REG-METRO": 25, "REG-INLAND": 55}  # minutes


def _solar_zenith_factor(hour_local: float) -> float:
    """0 at night, peaks at solar noon (~13:00 local for this bounding box)."""
    solar_noon = 13.0
    daylight_half_width = 7.0  # ~05:30 sunrise .. 20:30 sunset in summer-ish demo
    x = (hour_local - solar_noon) / daylight_half_width
    val = math.cos(x * math.pi / 2)
    return max(0.0, val) ** 1.3


@dataclass
class WeatherState:
    rng_state: dict = field(default_factory=dict)
    storm_active: bool = False
    storm_progress: float = 0.0  # 0..1 across regions Coastal->Inland
    base_wind_ms: float = 8.0
    wind_gust_phase: float = 0.0


class WeatherEngine:
    def __init__(self, rng):
        self.rng = rng
        self.state = WeatherState()
        self._minute_counter = 0.0

    def start_storm(self):
        self.state.storm_active = True
        self.state.storm_progress = 0.0

    def _wind_speed(self, region_id: str, minute_of_run: float) -> float:
        delay = FRONT_DELAY_MIN[region_id]
        t = max(0.0, minute_of_run - delay)
        # Diurnal-ish slow oscillation + gust noise, correlated by shared phase
        base = self.state.base_wind_ms + 2.5 * math.sin(t / 47.0 + hash(region_id) % 7)
        gust = self.rng.gauss(0, 0.6)
        speed = max(0.0, base + gust)
        if self.state.storm_active:
            # storm front boosts then depresses wind (turbulence -> shutdown region)
            front_center = self.state.storm_progress * 3  # regions index space
            region_pos = REGION_ORDER.index(region_id)
            dist = abs(front_center - region_pos)
            if dist < 1.0:
                speed += 10.0 * (1 - dist)  # gust spike as front arrives
            elif front_center > region_pos + 1.0:
                speed *= 0.35  # calm/lull after front passes (depressed wind)
        return round(speed, 2)

    def _cloud_cover(self, region_id: str, minute_of_run: float) -> float:
        delay = FRONT_DELAY_MIN[region_id]
        t = max(0.0, minute_of_run - delay)
        base = 35 + 25 * math.sin(t / 63.0 + hash(region_id) % 5)
        if self.state.storm_active:
            front_center = self.state.storm_progress * 3
            region_pos = REGION_ORDER.index(region_id)
            if abs(front_center - region_pos) < 1.5:
                base += 55
        return float(min(100.0, max(0.0, base + self.rng.gauss(0, 4))))

    def observe(self, region_id: str, local_dt: datetime, minute_of_run: float) -> dict:
        hour_local = local_dt.hour + local_dt.minute / 60.0
        cloud_pct = self._cloud_cover(region_id, minute_of_run)
        clear_sky_ghi = 950.0 * _solar_zenith_factor(hour_local)
        ghi = clear_sky_ghi * (1 - 0.85 * (cloud_pct / 100.0))
        temp_base = {"REG-COASTAL": 14.0, "REG-METRO": 16.0, "REG-INLAND": 18.0}[region_id]
        temp = temp_base + 6.0 * _solar_zenith_factor(hour_local) + self.rng.gauss(0, 0.4)
        wind_speed = self._wind_speed(region_id, minute_of_run)
        alert = None
        precip = 0.0
        if self.state.storm_active:
            front_center = self.state.storm_progress * 3
            region_pos = REGION_ORDER.index(region_id)
            if abs(front_center - region_pos) < 1.5:
                precip = round(self.rng.uniform(3, 12), 1)
                alert = "storm_front"
        return {
            "region_id": region_id,
            "temp_c": round(temp, 1),
            "wind_speed_ms": wind_speed,
            "wind_dir_deg": round(240 + self.rng.gauss(0, 15), 1),
            "ghi_wm2": round(max(0.0, ghi), 1),
            "cloud_cover_pct": round(cloud_pct, 1),
            "precip_mm_h": precip,
            "alert": alert,
        }

    def advance_storm(self, minutes_per_tick: float):
        if self.state.storm_active:
            self.state.storm_progress = min(1.0, self.state.storm_progress + minutes_per_tick / 90.0)
            if self.state.storm_progress >= 1.0:
                self.state.storm_active = False
