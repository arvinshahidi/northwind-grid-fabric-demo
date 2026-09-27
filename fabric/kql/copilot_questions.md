# Canned KQL Copilot questions

These are the natural-language questions the demo's `/query` endpoint (and a
real Fabric Copilot for KQL) can answer. Each maps to one of the `.kql` files
in this folder.

1. **"Which feeders are above 80% loading right now?"** -> `feeder_overload.kql`
2. **"Show wind farms whose output dropped more than 20% in 15 minutes"** -> `renewable_drop.kql`
3. **"Critical customers on currently outaged feeders"** -> `critical_customers_on_outage.kql`
4. **"Regions with predicted energy deficiencies during tonight's peak"** -> `predicted_deficits.kql`
5. **"Available peaker headroom vs reserve margin"** ->
   ```kql
   generation_telemetry
   | where asset_type == "gas_peaker"
   | summarize running_mw = sumif(active_power_mw, status == "running"),
               nameplate_mw = sum(nameplate_mw)
       by bin(event_time_utc, 5m)
   | extend headroom_mw = nameplate_mw - running_mw
   ```
6. **"What is the grid-wide reserve margin over the last hour?"** -> `live_balance.kql`
7. **"Show me all substations currently above 90% loading"**
   ```kql
   grid_telemetry
   | where element_type == "substation"
   | summarize arg_max(event_time_utc, loading_pct, region_id) by element_id
   | where loading_pct > 90
   ```
8. **"How many meters are reporting a last-gasp outage right now?"**
   ```kql
   meter_telemetry
   | summarize arg_max(event_time_utc, last_gasp) by meter_id
   | where last_gasp == true
   | count
   ```
9. **"What's the solar fleet output compared to nameplate over the last 30 minutes?"**
   ```kql
   generation_telemetry
   | where asset_type == "solar_farm" and event_time_utc > ago(30m)
   | summarize output_mw = avg(active_power_mw) by bin(event_time_utc, 5m)
   ```
10. **"Which open Activator alerts are still waiting for human approval?"**
    ```kql
    activator_alerts
    | where status == "open" and human_approval_required == true
    | project alert_id, rule_id, severity, message, recommended_action
    ```
