# Northwind Grid - Fabric Energy Grid Management demo

A complete, runnable local demo mirroring Microsoft Fabric's **Energy grid
management reference architecture** (Real-Time Intelligence):
https://learn.microsoft.com/en-us/fabric/real-time-intelligence/architectures/energy-grid-management

This is a **DEMO**, not a production EMS/ADMS. There is no closed-loop
control of breakers. Every automated action is labeled "recommended" and
requires human approval.

## The utility: Northwind Grid

A synthetic Pacific Northwest regional utility, seeded deterministically
(`SEED=42`) so every "baseline day" run is identical until a scenario is
injected:

- 3 regions (Coastal, Metro, Inland)
- 12 substations, 36 feeders
- 40 generation units (18 wind turbines / 3 farms, 8 solar farms, 6 gas
  peakers, 4 hydro, 2 battery storage, 2 biomass)
- 2,400 smart meters / customers, 8 field crews
- Nameplate generation ~1,150 MW against a peak demand target of ~850-950 MW

## Quickstart

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# Generate the deterministic topology + masters (once)
.venv\Scripts\python.exe -m topology.build_topology

# Run a scenario headless via the CLI, writing ndjson + a run summary
.venv\Scripts\python.exe -m simulator --scenario S1 --minutes 30 --speed 30 --out data/run1

# Or run the live control room: FastAPI + WebSocket backend, then open web/index.html
.venv\Scripts\python.exe -m uvicorn api.main:app --reload
# (in another terminal) cd web && python -m http.server 5500   -> http://localhost:5500

# Run the invariant test suite
.venv\Scripts\pytest -q
```

See `DEMO_SCRIPT.md` for an 8-minute booth walkthrough script.

## Repository layout

```
/schemas     Pydantic v2 models + exported JSON Schema (Draft 2020-12) for all 8 streams
/topology    Deterministic Northwind Grid topology + master data generator
/simulator   Clock, weather, load shapes, generation dispatch, scenarios, CLI runner
/ingest      In-memory event bus + ndjson sink (local stand-in for Eventstream)
/enrich      Phase 2 joins: generation<->ERP, meters<->CRM
/models      Phase 3 lightweight forecast/scoring models
/activate    Phase 4 Activator-style rule engine
/api         FastAPI control plane + WebSocket event stream
/web         Single-page control room UI (map, gauges, alerts, Copilot/KQL panel)
/fabric      Artifacts to take into a real Fabric tenant (KQL, rules, samples, wiring guide)
/tests       pytest invariant suite (physics + topology + scenario correctness)
/data/seed   Generated topology.json / asset_master.csv / customer_master.csv (ground truth)
```

## Mapping to the 11 Microsoft reference-architecture steps

| # | Microsoft step | Where it lives here |
|---|---|---|
| 1 | Stream generation IoT | `simulator/generation.py` (wind/solar/hydro/peaker/BESS physics) emits `generation_telemetry` via `ingest/bus.py` every tick |
| 2 | Stream grid events | `simulator/engine.py` aggregates feeder/substation load and emits `grid_telemetry` every tick |
| 3 | CRM via MQTT/Eventstream | `topology/build_topology.py` generates the CRM-style `customer_master.csv`; `simulator/engine.py` emits low-frequency `crm_context` events on-change, as if landed via MQTT into Eventstream |
| 4 | ERP via Data Factory/OneLake | `topology/build_topology.py` generates `asset_master.csv`; `simulator/engine.py` emits slowly-changing `erp_assets` snapshots, as if a Data Factory pipeline landed them in OneLake |
| 5 | Contextualize generation with ERP | `enrich/join.py: enrich_generation()` joins `generation_telemetry` to the ERP asset snapshot (nameplate MW, fuel, lat/lon, substation, feeder) |
| 6 | Aggregate generation picture | `simulator/engine.py` rolls generation up grid -> region -> substation -> feeder -> generator |
| 7 | Contextualize consumption, correlate with generation | `enrich/join.py: enrich_meter()` joins `meter_telemetry` to CRM metadata; `models/forecast.py` correlates supply vs demand in real time |
| 8 | Score ML models | `models/forecast.py`: deficiency forecast (1h/4h), demand-spike probability, renewable forecast, grid stability score -- run every 12 ticks and written to a `predictions` stream |
| 9 | Activator notifications | `activate/rules.py`: 6 rules (demand>90%, forecast 15% deficit, renewable drop, redistribute, feeder overload, critical-customer outage), debounced, each producing an `activator_alerts` event with a recommended action requiring human approval |
| 10 | Real-Time Dashboard drill-down | `web/index.html`: Grid Overview -> Generation/Consumption -> Predictions -> Activator Inbox, all live via `/grid/snapshot` + WebSocket `/stream/events` |
| 11 | Power BI-style business view | `web/index.html`'s Architecture tab plus the Fabric export in `/fabric` (see `fabric/FABRIC_WIRING.md` step 6 for how this becomes a real Power BI Direct Lake report) |

## Event schemas (the Fabric contract)

All 8 streams are defined once in `schemas/models.py` (Pydantic v2) and
exported to JSON Schema Draft 2020-12 via `schemas/export_schemas.py`:
`generation_telemetry`, `grid_telemetry`, `meter_telemetry`, `crm_context`,
`erp_assets`, `weather_observations`, `predictions`, `activator_alerts`.
Every event is joinable: `meter_id -> feeder_id -> substation_id ->
region_id`.

## Scenarios

Seven scripted, triggerable scenarios (`--scenario S1`..`S7`, or
`POST /sim/scenario/{id}`), each with a 30-second presenter narrative card in
`simulator/scenarios.py`:

| ID | Name | What it proves |
|---|---|---|
| S1 | BASELINE_DAY | Calm weekday, dashboard green, warm-up state |
| S2 | EVENING_PEAK_90 | Demand crosses 90% of available capacity -> immediate alert |
| S3 | DEFICIT_15 | Peak + wind lull -> ~15% forecast supply shortfall -> proactive alert |
| S4 | WIND_FARM_TRIP | Two Coastal turbines trip -> frequency dip -> redispatch recommendation |
| S5 | STORM_TRACK | Weather polygon crosses regions -> correlated feeder outages -> crew + load-shed recommendations |
| S6 | SOLAR_RAMP_CLOUD | Fast cloud field -> solar drops ~40% in 10 sim-minutes -> ramp-risk warning |
| S7 | DEMAND_RESPONSE | Operator approves a DR recommendation -> load drops, reserve margin recovers |

## Fabric-ready export

`/fabric` contains everything needed to move this from local to a real
tenant without changing property names: `eventstream_samples/` (200-line
ndjson samples per stream), `kql/` (Eventhouse table DDL + 6 Activator-feeding
queries + 10 canned Copilot questions), `activator_rules.json`, `onelake/`
(CSV masters + shortcut instructions), `connectors/eventstream_publisher.py`
(a ready-to-use EventBus subscriber that live-forwards simulator events to
real Fabric Eventstreams via `azure-eventhub`), and `FABRIC_WIRING.md` (the
conceptual wiring guide).

**Going live in a real tenant?** See `fabric/FABRIC_SETUP_GUIDE.md` for a
concrete day-by-day (5-day) plan: create the Eventhouse + tables, wire live
Eventstream ingestion from this simulator (`FABRIC_ES_*` env vars, opt-in and
additive -- the local demo is unaffected if unset), build Activator rules,
build the Real-Time Dashboard + Power BI report, and rehearse the demo
script against real Fabric.

## Tests / invariants

`tests/test_invariants.py` (run via `pytest -q`) checks the synthetic world
is physically sane: CRM/ERP completeness and join integrity, nameplate vs
peak-demand ratios, solar-at-night and wind cut-in/cut-out boundaries,
meter-to-feeder load diversity tolerance, energy-balance residual, no
future timestamps, and that S2/S3/S4 actually produce the numbers their
scenario names promise.
