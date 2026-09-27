# FABRIC_WIRING.md - swapping the local bus for real Fabric

This demo runs entirely locally (Python simulator -> in-process event bus ->
FastAPI/WebSocket -> a browser control room). Every event property name was
chosen to match the Fabric Eventstream / Eventhouse contract exactly, so
moving to a real tenant is a **rewiring exercise, not a rewrite**.

## 1. Simulator -> Eventstream

Today: `simulator/__main__.py` (or `api/main.py`'s background thread) calls
`ingest.bus.EventBus.publish_many(stream_name, events)`.

To go real:
- Stand up an **Azure Event Hub** (or use a **Fabric Eventstream custom
  endpoint**) per stream, or one Event Hub with `stream` as a routing
  property.
- Replace `EventBus.publish` with an Event Hub producer client send, keeping
  the exact same JSON payload shape (the pydantic models in `schemas/models.py`
  already serialize to that shape via `.model_dump_json()`).
- Point a Fabric **Eventstream** at the Event Hub as its source.

```python
# ingest/bus.py -> swap NdjsonSink/local subscriber for:
from azure.eventhub import EventHubProducerClient, EventData
producer = EventHubProducerClient.from_connection_string(CONN_STR, eventhub_name=stream)
def publish(self, stream, event):
    batch = producer.create_batch()
    batch.add(EventData(json.dumps(event)))
    producer.send_batch(batch)
```

## 2. Eventstream -> Eventhouse

- In the Fabric Eventstream designer, add a destination per stream pointing
  at an **Eventhouse** (KQL) database.
- Run `fabric/kql/create_tables.kql` once against that Eventhouse database to
  create the 8 tables with the matching schema.
- Use the Eventstream's built-in JSON mapping (property name = column name)
  since our field names already match 1:1.

## 3. CRM / ERP -> OneLake -> Eventhouse shortcut

- Land `fabric/onelake/asset_master.csv` and `customer_master.csv` (today:
  synthetic; in production: your real ERP/CRM extracts) into a Lakehouse via
  **Data Factory** (see `fabric/onelake/README.md`). Alternatively, stream
  `crm_context`/`erp_assets` live through Eventstream like the other raw
  streams (see `fabric/FABRIC_SETUP_GUIDE.md` Day 2) -- either path works,
  since the enrichment in step 3b below reads from the same table either way.

### 3b. Native enrichment (Phase 2, done inside Fabric, not Python)

- Run `fabric/kql/phase2_enrichment.kql` once against the Eventhouse. It
  creates `generation_telemetry_enriched` and `meter_telemetry_enriched`
  tables with **update policies** that automatically join every new row of
  `generation_telemetry`/`meter_telemetry` against the latest `erp_assets`/
  `crm_context` row, in real time, inside the Eventhouse.
- This replaces `enrich/join.py`'s Python dict joins for the real-Fabric
  path -- that module remains only as the fallback used by the local
  `web/index.html` control room when running fully offline.

## 4. Models -> native KQL functions (done, no notebook needed)

- Run `fabric/kql/phase3_native_scoring.kql` once against the Eventhouse.
  It creates two KQL functions that replace `models/forecast.py`'s Python
  heuristics for the real-Fabric path:
  - `AvailableCapacityNow()` -- live demand vs. accredited capacity (ERP
    nameplate joined to each generator's current on/offline status).
  - `ForecastGridBalance(horizonMinutes)` -- forward-looking demand/
    generation forecast using KQL's built-in `series_decompose_forecast()`,
    a genuine Fabric/ADX time-series forecasting primitive.
- `fabric/kql/demand_vs_capacity.kql` and `fabric/kql/predicted_deficits.kql`
  call these functions directly -- point your Real-Time Dashboard tiles and
  Activator rules at those two files.
- No `predictions` Eventstream/table is needed for the real-Fabric path;
  `models/forecast.py` remains only as the offline fallback for the local
  control room.

## 5. Activator

- Import `fabric/activator_rules.json` as your rule catalogue.
- For each rule, create a **Fabric Activator** rule pointed at the relevant
  KQL query in `fabric/kql/*.kql` (each file's header comment says which
  rule it feeds).
- Configure the Activator action as a **Teams/email notification with an
  Approve/Dismiss adaptive card** -- this preserves the "recommended action,
  human approval required" pattern from the local demo; do **not** wire
  Activator directly to a breaker/SCADA control action.

## 6. Real-Time Dashboard + Power BI Direct Lake

- Build a **Fabric Real-Time Dashboard** on top of the Eventhouse database
  using the same KQL queries in `fabric/kql/`, tiled as: grid overview ->
  region -> substation -> feeder -> meter/asset drill-down (Microsoft step
  10).
- For the executive/business view (step 11), build a **Power BI report in
  Direct Lake mode** against the Eventhouse database (via the built-in
  "Create Power BI report" from Eventhouse), so business users get the same
  live numbers without a separate copy/import step.

## Keeping the contract stable

Everything in `schemas/models.py` and `schemas/json_schema/*.schema.json` is
the source of truth for property names. As long as a real Fabric pipeline
uses those same names end to end, none of `enrich/`, `models/`, `activate/`,
or the KQL in `fabric/kql/` need to change -- only the transport (local bus
-> Event Hub/Eventstream) and storage (in-memory/DuckDB -> Eventhouse) layers
are swapped.
