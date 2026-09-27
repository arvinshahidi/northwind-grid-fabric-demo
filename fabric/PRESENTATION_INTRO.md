# Presentation opening — read/show this before the live demo

Use this as your opening slide talking points or literally read it near
verbatim. All numbers below are the actual generated topology (seed=42,
fully reproducible), not rounded marketing figures.

---

## The utility: Northwind Grid

A synthetic regional utility built to demo Microsoft Fabric's official
**Energy Grid Management reference architecture** end-to-end, live, in a
real Fabric tenant.

- **Territory:** Pacific Northwest-style coastal-to-inland slice
  (lat 45.2-48.8, lon -124.6 to -121.0)
- **3 regions:** Coastal, Metro, Inland
- **12 substations** (4 per region)
- **36 feeders** (3 per substation, 18-34 MVA capacity each)
- **2,400 smart meters** (200 per substation), residential/commercial/
  industrial mix, ~1.5% flagged critical facilities, ~2% medical-baseline
- **8 field crews**

## Generation fleet — 40 assets, ~1,190 MW nameplate

| Type | Count | Rating range (MW each) | Approx. total |
|---|---|---|---|
| Wind turbines (3 farms) | 18 | 2.5-4.5 | ~63 MW |
| Solar farms | 8 | 15-25 | ~160 MW |
| Gas peakers | 6 | 80-120 | ~600 MW |
| Hydro | 4 | 45-75 | ~240 MW |
| Battery storage (BESS) | 2 | 40-60 | ~100 MW |
| Biomass | 2 | 12-18 | ~30 MW |

- **Peak demand:** ~850-950 MW under stress conditions (verified live —
  we hit 884 MW / 109% of available capacity in today's test run)
- **Baseline healthy demand:** ~350-450 MW, reserve margin 100%+

Say: *"There's real headroom in this fleet, but not infinite headroom —
enough that a wind lull stacked on an evening peak genuinely breaches the
90% and 15% thresholds you're about to see fire, live."*

## Data sources feeding this live, right now

Six live Fabric Eventstreams, streaming synthetic-but-physically-modeled
telemetry every 5 simulated seconds:

1. **generation_telemetry** — per-asset output, status, wind speed,
   irradiance, battery state of charge
2. **grid_telemetry** — substation and feeder load, loading %, voltage,
   frequency, status
3. **meter_telemetry** — smart meter readings, voltage violations,
   last-gasp/restoration signals
4. **crm_context** — customer identity, rate class, critical/medical flags
   (simulating an MQTT-fed CRM feed)
5. **erp_assets** — nameplate capacity, install date, maintenance schedule
   (simulating a Data Factory / OneLake ERP sync)
6. **weather_observations** — temperature, wind, solar irradiance, cloud
   cover per region

All landing in one **Fabric Eventhouse (KQL database)**, enriched and
forecast **natively in KQL** (update policies + `series_decompose_forecast`)
— not pre-computed in Python and shipped in.

## What you're about to see, live

- A **Real-Time Dashboard** with grid → region → substation → feeder
  drill-down
- **Fabric Activator** rules firing real Teams/email alerts off native KQL
  queries — always phrased as a recommendation requiring human approval,
  never automatic control
- A **Data Agent** answering natural-language operator questions, grounded
  in this exact live data, refusing to guess when data doesn't support an
  answer
- A **Power BI** business view over the same live data, no copy/import step

Say: *"Everything from here on is live against a real Fabric tenant — the
same architecture pattern Microsoft documents for production utilities, at
demo scale."*
