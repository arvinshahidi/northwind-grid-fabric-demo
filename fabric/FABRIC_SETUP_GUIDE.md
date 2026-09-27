# FABRIC_SETUP_GUIDE.md - going live in a real Fabric tenant (5-day plan)

This is the step-by-step guide for wiring the Northwind Grid simulator into
**your real Fabric workspace** so you can demo it to customers using actual
Eventstream, Eventhouse, Activator, Real-Time Dashboard, and Power BI --
not just the local `web/index.html` control room.

Everything here assumes you already have a Fabric-enabled workspace with
capacity assigned. All portal steps are manual (I can't click through your
tenant for you); the connector code that pushes live data in is already
written and ready in `fabric/connectors/eventstream_publisher.py`.

---

## Day 1 - Workspace + Eventhouse + tables

1. In the Fabric portal, create (or reuse) a workspace, e.g. `Northwind Grid Demo`.
2. Create a new **Eventhouse** item, e.g. `eh-northwind-grid`. This gives you
   a KQL database of the same name by default.
3. Open the KQL QuerySet against that database and run
   `fabric/kql/create_tables.kql` to create all 8 tables
   (`generation_telemetry`, `grid_telemetry`, `meter_telemetry`,
   `crm_context`, `erp_assets`, `weather_observations`, `predictions`,
   `activator_alerts`) with the exact schema the simulator emits.
4. Sanity check: `.show tables` in the KQL QuerySet should list all 8.

**End of Day 1 checkpoint:** empty tables exist with the right schema.

---

## Day 2 - Live ingestion (Eventstream, Custom App source) -- RAW telemetry only

**Important architecture change:** only forward *raw* telemetry from the
local simulator. Enrichment (Phase 2) and forecasting (Phase 3) now happen
**natively inside Fabric** via KQL (see the new step 2b below) -- so the
local Python `enrich/join.py` and `models/forecast.py` are demo-fallback
logic for the offline `web/index.html` control room only, not part of the
real-Fabric wiring. Do **not** wire `predictions` or `activator_alerts` as
Eventstreams; Fabric computes and detects those itself.

Wire these streams (in this order):

`generation_telemetry`, `grid_telemetry`, `meter_telemetry`,
`weather_observations`, `crm_context`, `erp_assets`.

For **each** of those streams:

1. Create a new **Eventstream** item, e.g. `es-generation-telemetry`.
2. Add a source: **Custom Endpoint** -> protocol **Event Hub** (this is the
   Event-Hub-compatible custom endpoint; NOT the AMQP or Kafka protocol
   options). Fabric will generate a connection string -- copy it.
3. Add a destination: your Eventhouse (`eh-northwind-grid`), table =
   matching stream name (e.g. `generation_telemetry`), with **JSON format,
   direct field mapping** (property names already match the table columns
   1:1, so use "no transformation" / direct mapping).
4. Publish the Eventstream.

Back on your machine:

5. Install the optional dependency:
   ```
   .venv\Scripts\pip install -r requirements-fabric.txt
   ```
6. Copy `fabric\.fabric.env.example` to `fabric\.fabric.env` and paste each
   stream's connection string after its matching key
   (`FABRIC_ES_GENERATION_TELEMETRY=...`, etc). Leave
   `FABRIC_ES_PREDICTIONS` and `FABRIC_ES_ACTIVATOR_ALERTS` **blank** --
   those are intentionally not wired.
7. Load the env vars and test each stream one at a time as you wire it:
   ```powershell
   . .\fabric\load_fabric_env.ps1
   .\.venv\Scripts\python.exe -m fabric.connectors.test_connection generation_telemetry
   ```
8. Once all 6 raw streams are wired, run the real simulator (same terminal,
   so it inherits the env vars):
   ```powershell
   .\.venv\Scripts\python.exe -m simulator --scenario S1 --minutes 10 --speed 60 --out data/fabric_test
   ```
   You should see a console line:
   `[fabric] live-forwarding streams to real Fabric Eventstreams: crm_context, erp_assets, generation_telemetry, grid_telemetry, meter_telemetry, weather_observations`
9. Back in the Fabric portal, open the Eventhouse KQL QuerySet and run:
   ```kql
   generation_telemetry | take 20
   ```
   You should see real rows landing within a few seconds.

**End of Day 2 checkpoint (part 1):** running the simulator locally makes
raw telemetry rows appear in your real Fabric Eventhouse tables within
seconds.

### Day 2b - native enrichment (Phase 2, inside Fabric)

In the Eventhouse KQL QuerySet, run these two scripts **once** (they create
tables + update policies that keep enriching every new row automatically
from then on):

1. `fabric/kql/phase2_enrichment.kql` -- creates
   `generation_telemetry_enriched` (joined live with `erp_assets` nameplate
   data) and `meter_telemetry_enriched` (joined live with `crm_context`
   rate class / critical-facility flags).
2. `fabric/kql/phase3_native_scoring.kql` -- creates two KQL functions:
   `AvailableCapacityNow()` (live demand vs. accredited capacity) and
   `ForecastGridBalance(horizonMinutes)` (forward-looking demand/generation
   forecast using KQL's built-in `series_decompose_forecast()`).

Verify:
```kql
generation_telemetry_enriched | take 5     // should show nameplate_mw, oem, etc. alongside telemetry
meter_telemetry_enriched | take 5          // should show rate_class, critical_facility alongside telemetry
AvailableCapacityNow()                     // should print a single row: available_capacity_mw, current_demand_mw, demand_pct_of_capacity
ForecastGridBalance(240)                   // should print a forecasted demand/gen/deficit 4h out
```

**End of Day 2 checkpoint (part 2):** enrichment and forecasting run inside
Fabric itself, computed from the raw telemetry you're streaming in -- not
pre-computed by local Python.



---

## Day 3 - Activator rules (real detection, running on native Fabric queries)

This is the actual Activator story: Fabric detects the condition itself by
running the KQL query, not by receiving a pre-computed alert from outside.

For each rule in `fabric/activator_rules.json`:

1. Open the relevant KQL query (`fabric/kql/*.kql` -- each file's header
   comment says which rule it feeds; RULE-DEMAND-90 ->
   `demand_vs_capacity.kql`, RULE-DEFICIT-15 -> `predicted_deficits.kql`,
   RULE-RENEWABLE-DROP -> `renewable_drop.kql`, RULE-FEEDER-OVERLOAD ->
   `feeder_overload.kql`, RULE-CRITICAL-OUTAGE ->
   `critical_customers_on_outage.kql`) in the Eventhouse KQL QuerySet, run
   it once to confirm it returns rows, then use **Set alert** (or create a
   new **Fabric Activator** item pointed at this query on a schedule, e.g.
   every 1-2 minutes).
2. Configure the condition using the `metric`/`threshold`/`comparison`
   fields from `activator_rules.json` (e.g. RULE-DEMAND-90: fires when the
   query returns any row -- `demand_vs_capacity.kql` already filters to
   `demand_pct_of_capacity > 90`).
3. Configure the action as a **Teams message or email** using the
   `action` text from `activator_rules.json` as the message body, and make
   the card explicitly say "Recommended action -- requires approval" (do
   **not** wire this to any control/automation action).
4. Repeat for all 6 rules: RULE-DEMAND-90, RULE-DEFICIT-15,
   RULE-RENEWABLE-DROP, RULE-REDISTRIBUTE, RULE-FEEDER-OVERLOAD,
   RULE-CRITICAL-OUTAGE. (RULE-REDISTRIBUTE can reuse `live_balance.kql`
   grouped by region -- add a `by region_id` and an imbalance-threshold
   filter if you want it as a 6th distinct rule; it's the one rule without
   a dedicated file today.)
5. Test each rule by running the matching scenario locally with live
   forwarding on (e.g. `--scenario S2` for RULE-DEMAND-90, `--scenario S3`
   for RULE-DEFICIT-15) and confirm the Teams/email notification fires --
   driven entirely by Fabric re-running the KQL query against the raw
   telemetry you're streaming in, on its own schedule.

**End of Day 3 checkpoint:** running S2 locally with live forwarding on
produces a real Teams/email alert from Fabric Activator within a minute or
two -- and you can point to the exact KQL query that detected it.

---

## Day 4 - Real-Time Dashboard + Power BI

1. Create a **Real-Time Dashboard** item in the workspace.
2. Add tiles built directly from the KQL in `fabric/kql/`:
   - `live_balance.kql` -> grid-wide supply/demand/frequency/reserve tile
   - `feeder_overload.kql` -> feeder loading table/heatmap tile
   - `renewable_drop.kql` -> wind farm output trend tile
   - `predicted_deficits.kql` -> 1h/4h deficit chart tile
   - `critical_customers_on_outage.kql` -> critical-customer outage table
   - `live_proof_ingest.kql` -> **stat tile**: events ingested in the last
     5 min + seconds since the last event (proves the pipeline is live,
     not a static screenshot)
   - `live_proof_totals.kql` -> **stat tile**: total system load MW, total
     generation MW, and substations currently reporting
3. For the two `live_proof_*` tiles, use the **"Stat"** (or "Card") visual
   type instead of a time chart -- each query returns a single row, so pin
   individual columns (e.g. `events_last_5min`, `total_gen_mw`) as big
   numbers near the top of the dashboard. These are the tiles to point at
   first when someone asks "is this actually live data?"
4. Arrange tiles to mirror the drill-down story: grid overview -> region ->
   substation -> feeder -> meter/asset (Microsoft step 10).
5. From the Eventhouse database, use **"Create Power BI report"** (Direct
   Lake mode) to build the executive/business view (Microsoft step 11) --
   same live numbers, no separate copy/import step.

**End of Day 4 checkpoint:** a Real-Time Dashboard and a Power BI report are
both live against the same Eventhouse data the simulator is feeding.

---

## Day 5 - Rehearse against real Fabric

1. Run through `DEMO_SCRIPT.md` exactly as written, but with live forwarding
   turned on (all `FABRIC_ES_*` env vars set) so every scenario injection
   (S1-S7) shows up in the real Fabric Real-Time Dashboard and fires real
   Activator alerts, instead of (or alongside) the local `web/index.html`
   control room.
2. Time it -- confirm the 8-minute script still fits with the extra beat of
   switching over to the Fabric portal tabs.
3. Have a fallback: keep the local control room (`web/index.html`) open in
   a second tab in case of tenant/network flakiness during the live demo --
   it runs entirely offline and tells the identical story.

---

## Recap: why this is low-risk to wire up

- No schema changes needed: `fabric/connectors/eventstream_publisher.py`
  sends the exact same JSON your local demo already produces and validates
  in `tests/test_invariants.py`.
- It's opt-in and additive: with no `FABRIC_ES_*` env vars set, the
  simulator and API behave exactly as before (see
  `maybe_attach_fabric_sink()` -- it no-ops safely on missing config or
  missing `azure-eventhub`).
- You can wire one stream at a time and verify data landing before moving
  to the next, rather than an all-or-nothing cutover.
