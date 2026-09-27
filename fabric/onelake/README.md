# OneLake landing folder

These CSVs simulate what Azure Data Factory would land in a OneLake
lakehouse from the utility's ERP and CRM systems (Microsoft step 4:
"Sync ERP asset/inventory metadata").

| File | Source system (real world) | Role |
|---|---|---|
| `asset_master.csv` | ERP | Generation asset nameplate/maintenance metadata used to enrich `generation_telemetry` (step 5) |
| `customer_master.csv` | CRM | Meter/customer/rate-class metadata used to enrich `meter_telemetry` (step 7) |
| `topology.csv` | GIS / OMS | Flattened join path: `meter_id -> feeder_id -> substation_id -> region_id`, used for every drill-down aggregation (step 6) |

## How to land these with a real Fabric tenant

1. Create a Lakehouse (e.g. `lh_northwind_grid`).
2. Use a **Data Factory Copy Data** activity (or a pipeline on a schedule) to
   land these CSVs as Delta tables in the lakehouse `Files`/`Tables` area.
   In production this would instead pull from the utility's real ERP/CRM/GIS
   via a connector.
3. Create a **OneLake shortcut** from your Eventhouse database pointing at
   the lakehouse tables, so KQL queries can join streaming telemetry against
   this reference data without copying it.
4. Re-run the enrichment joins in `enrich/join.py` as KQL `lookup` operators
   instead of Python dict joins -- the column names are identical so the KQL
   is a near 1:1 translation.
