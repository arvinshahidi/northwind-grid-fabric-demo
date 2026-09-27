# Provoking Fabric Activator alerts — exact commands

Run all of these from the repo root, in a terminal with the venv activated
and Fabric env vars loaded in that **same** window:

```powershell
cd C:\Users\arvinshahidi\projects\northwind-grid-fabric-demo
& .\.venv\Scripts\Activate.ps1
. .\fabric\load_fabric_env.ps1
```

You should see: `Loaded Fabric Eventstream connection strings for: ...` (6
names). If you don't see that line before running the simulator, the
`[fabric] live-forwarding streams...` line won't print either, and nothing
will reach Fabric.

**Important:** in the Fabric portal, make sure the Eventstream item(s) show
as running/resumed, not paused, before every session — check the top
toolbar for a "Resume" button.

---

## 1. Continuous baseline (keep the dashboard "alive" between demos)

```powershell
$env:FABRIC_BACKFILL_MINUTES = $null
.\.venv\Scripts\python.exe -m simulator --scenario S1 --minutes 1440 --speed 30 --sleep --out data\fabric_live
```
Runs ~48 real minutes of healthy baseline day, real-time-stamped. Leave this
running in its own window whenever you're not actively provoking a
scenario, so dashboard tiles keep moving.

## 2. RULE-DEMAND-90 (reactive: demand > 90% of available capacity)

```powershell
$env:FABRIC_BACKFILL_MINUTES = $null
.\.venv\Scripts\python.exe -m simulator --scenario S2 --minutes 120 --speed 30 --start-hour 17 --sleep --out data\provoke_s2
```
- **Must** use `--start-hour 17` (or similar 16-19 range) — this scenario is
  timed to the evening peak; starting at the default 05:00 will never
  breach the threshold.
- Runs ~4 real minutes. Watch for a Teams/email notification and check:
  `AvailableCapacityNow()` → `demand_pct_of_capacity` should exceed 90.

## 3. RULE-DEFICIT-15 (proactive: forecast 15% shortfall in evening peak)

```powershell
$env:FABRIC_BACKFILL_MINUTES = $null
.\.venv\Scripts\python.exe -m simulator --scenario S3 --minutes 240 --speed 60 --start-hour 16.5 --sleep --out data\provoke_s3
```
- Also **requires** `--start-hour 16.5` (or similar) to land in the peak
  window — this layers a coastal wind lull on top of the evening ramp.
- Runs ~4 real minutes. Check: `ForecastGridBalanceByRegion(240)` and
  `ForecastGridBalance(240)` for `deficit_pct >= 15`.

## 4. RULE-RENEWABLE-DROP (wind farm trip)

```powershell
$env:FABRIC_BACKFILL_MINUTES = $null
.\.venv\Scripts\python.exe -m simulator --scenario S4 --minutes 60 --speed 30 --sleep --out data\provoke_s4
```
- Not tied to time-of-day — the turbine trip is scenario-injected
  regardless of start hour. Default start time is fine.
- Runs ~2 real minutes.

## 5. RULE-FEEDER-OVERLOAD / RULE-CRITICAL-OUTAGE (storm track)

```powershell
$env:FABRIC_BACKFILL_MINUTES = $null
.\.venv\Scripts\python.exe -m simulator --scenario S5 --minutes 90 --speed 30 --sleep --out data\provoke_s5
```
- Weather-driven, not time-of-day driven. Runs ~3 real minutes.
- Check the "Critical Customers on Outage" dashboard tile and
  `RULE-CRITICAL-OUTAGE` / `RULE-FEEDER-OVERLOAD` Activator run history.

## 6. Recovery beat (optional close-out)

```powershell
$env:FABRIC_BACKFILL_MINUTES = $null
.\.venv\Scripts\python.exe -m simulator --scenario S7 --minutes 60 --speed 30 --start-hour 17 --sleep --out data\provoke_s7
```
- Demonstrates the recommend → approve → recover loop; reserve margin
  should visibly recover on the Grid Overview tile.

---

## Quick verification queries (run in the KQL queryset any time)

```kql
AvailableCapacityNow()
```
```kql
ForecastGridBalance(60)
ForecastGridBalance(240)
```
```kql
ForecastGridBalanceByRegion(240)
```
```kql
PeakerHeadroomNow()
```
```kql
grid_telemetry | where element_type == "feeder" | summarize arg_max(event_time_utc, loading_pct) by element_id | where loading_pct > 80
```

## Known gotchas (already hit and fixed once — don't repeat)

- Env vars only persist in the exact terminal window you set them in.
- `python -m simulator` may resolve to the wrong interpreter — always use
  `.\.venv\Scripts\python.exe` explicitly.
- The Eventstream item can be paused in the portal even when your script is
  sending fine — always check it's resumed before a session.
- S2/S3 need `--start-hour` in the evening-peak range (16-19) to actually
  breach their thresholds; the default 05:00 start will not trigger them.
