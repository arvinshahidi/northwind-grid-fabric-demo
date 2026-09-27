# DEMO_SCRIPT.md - 8 minute booth walkthrough

Audience: energy/utility operator, or a Microsoft AE narrating Fabric Real-Time
Intelligence capabilities using this local stand-in. Total runtime target:
~8 minutes. All timings assume the default 30x-60x sim speed.

**Setup before the booth opens:**
```
python -m venv .venv && .venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python.exe -m topology.build_topology     # once, generates data/seed
.venv\Scripts\python.exe -m uvicorn api.main:app --reload
# open web/index.html in a browser (or `python -m http.server` from web/)
```

---

## 0:00 - 0:45 | Open on the Architecture tab

Say: "This is Northwind Grid, a synthetic 900MW regional utility we built to
demo Microsoft Fabric's Energy Grid Management reference architecture end to
end -- entirely local, no cloud costs, same event contract as production
Fabric." Point at the 4 phases and the 11 numbered steps, and the live
events/sec counters already ticking from the baseline simulation.

## 0:45 - 2:00 | Grid Overview tab, Scenario S1 (BASELINE_DAY)

Read the S1 narrative card:

> "BASELINE DAY - Calm midweek morning ramp. Reserve margins are healthy
> across all three regions, wind is moderate at the coast, and the dashboard
> is all green. This is our warm-up state before we inject any stress."

Point at: the map (3 regions, 12 substations), the supply/demand gauge,
frequency proxy sitting on 60Hz, reserve margin comfortably high.

## 2:00 - 3:30 | Inject S2 (EVENING_PEAK_90)

`POST /sim/scenario/S2` (or click the scenario button in the UI).

> "EVENING PEAK 90% - As the evening peak builds between 5 and 8pm, demand
> climbs past 90% of available capacity. Activator immediately fires the
> exact Microsoft rule: 'Demand exceeds 90% of available capacity.' The
> recommended actions are to start the gas peakers, discharge the battery,
> and call a commercial demand-response event -- all pending human approval."

Switch to the **Activator Inbox** tab, show the alert, the evidence (metric,
value, threshold), and the Approve/Dismiss buttons. Emphasize: nothing is
auto-executed.

## 3:30 - 4:45 | Inject S3 (DEFICIT_15)

> "DEFICIT 15% - We layer a coastal wind lull on top of the evening peak. The
> 4-hour forecast model now shows roughly a 15% supply shortfall during
> tonight's peak window. Activator fires 'Forecast 15% supply shortfall
> during peak evening hours' as a proactive, not reactive, warning -- giving
> operators hours of lead time instead of minutes."

Switch to the **Predictions** tab: show the 1h/4h deficit chart with the 90%
and 15% threshold lines, and point out this fired *before* the deficit
actually happened.

## 4:45 - 5:45 | Inject S4 (WIND_FARM_TRIP)

> "WIND FARM TRIP - Two turbines at our Coastal wind farm trip
> simultaneously. You'll see the frequency proxy dip and nearby feeder
> voltages move. The system recommends redispatch from peakers and hydro --
> it does NOT auto-trip any customers."

Switch to **Generation** tab: show the two turbines flip to `offline`,
renewable share drop, curtailment/redispatch recommendation appear.

## 5:45 - 6:45 | Inject S5 (STORM_TRACK)

> "STORM TRACK - A weather front is tracked moving from Coastal into Metro.
> As it crosses, wind and solar output collapse and two feeders show
> correlated last-gasp outages. Field crews are recommended for dispatch,
> and an emergency load-shed recommendation only appears if frequency or
> reserve margin crosses the hard emergency threshold."

Switch to **Consumption** tab: show the feeder heat map going dark, the
critical-customer count on the affected feeders (the exact join path:
meter -> feeder -> substation -> region), and the crew recommendation list.

## 6:45 - 7:15 | Copilot / KQL panel

Type (or click a canned question): *"Show me all regions with predicted
energy deficiencies during tomorrow's peak hours."* Show the compiled
KQL-like filter and the result table. Mention this is the local stand-in for
Fabric's Copilot-for-KQL experience against a real Eventhouse.

## 7:15 - 8:00 | Inject S7 (DEMAND_RESPONSE) and close

Approve the recommended DR action from the Activator inbox.

> "DEMAND RESPONSE ACCEPTED - The operator approves a recommended
> demand-response event. Commercial load drops 5-8% over 10 minutes, reserve
> margin recovers, and the alert clears -- showing the full recommend ->
> approve -> recover loop without ever touching a breaker."

Close: "Every property name here maps directly onto a Fabric Eventstream /
Eventhouse schema -- see `/fabric` in the repo for the exact KQL, Activator
rule definitions, and OneLake landing files. Swapping this local bus for
Event Hub + Fabric Eventstream is a rewiring exercise, not a rewrite."

---

### Optional extra beat (S6, if time allows)

> "SOLAR RAMP / CLOUD - A fast-moving cloud field crosses the solar farms and
> output drops about 40% in just 10 simulated minutes. This is the classic
> renewable ramp-risk story Activator is built to catch before it becomes a
> deficit."
