"""
Simulation clock. Wall-clock -> simulated time with a speed multiplier.
Tick length is fixed at 5 simulated seconds; `speed` compresses wall time.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

LOCAL_TZ_OFFSET_HOURS = -7  # Pacific Daylight Time offset from UTC (demo constant)
TICK_SECONDS = 5


@dataclass
class SimClock:
    sim_time_utc: datetime
    speed: float = 60.0
    tick_seconds: int = TICK_SECONDS
    ticks_elapsed: int = 0

    @classmethod
    def starting_at(cls, local_dt: datetime, speed: float = 60.0) -> "SimClock":
        utc_dt = local_dt - timedelta(hours=LOCAL_TZ_OFFSET_HOURS)
        utc_dt = utc_dt.replace(tzinfo=timezone.utc)
        return cls(sim_time_utc=utc_dt, speed=speed)

    def tick(self) -> datetime:
        self.sim_time_utc += timedelta(seconds=self.tick_seconds)
        self.ticks_elapsed += 1
        return self.sim_time_utc

    @property
    def local_time(self) -> datetime:
        return self.sim_time_utc + timedelta(hours=LOCAL_TZ_OFFSET_HOURS)

    def iso_utc(self) -> str:
        return self.sim_time_utc.isoformat()

    def iso_local(self) -> str:
        return self.local_time.isoformat()

    @property
    def wall_sleep_seconds(self) -> float:
        """How long to sleep in real time per tick, given speed multiplier."""
        return max(0.0, self.tick_seconds / self.speed)


def default_start(now: datetime | None = None) -> datetime:
    """Next weekday 05:00 local, per spec default."""
    now = now or datetime.now()
    d = now.replace(hour=5, minute=0, second=0, microsecond=0)
    if d <= now:
        d += timedelta(days=1)
    while d.weekday() >= 5:  # Sat=5, Sun=6
        d += timedelta(days=1)
    return d
