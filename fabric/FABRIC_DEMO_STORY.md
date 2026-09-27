# Northwind Grid — live Fabric demo story (for a non-grid-expert presenter)

This is a talk-track for presenting to **grid/energy industry experts**,
written so you don't need a power-systems background to sound credible.
Every technical term you'll need to say out loud is explained inline.
Target runtime: ~12-15 minutes.

Companion files:
- `fabric/PROVOKE_ALERTS.md` — the exact terminal commands to start live
  streaming and trigger each alert, with timing.
- `fabric/DATA_AGENT_DEMO_QUESTIONS.md` — the 5 questions to type into the
  Data Agent, no commentary (say them cold, let the answers land).

---

## Part 1 — Open on the architecture reference (2 min)

Show the Microsoft Learn page (or a printout/screenshot) for **"Energy grid
management reference architecture"** under Fabric Real-Time Intelligence.

**Say:**
> "Microsoft publishes a reference architecture for how a utility should run
> its grid data on Fabric. It has four phases: ingest raw telemetry, enrich
> and correlate it, score lightweight forecasting models, then visualize and
> activate alerts. We didn't invent our own pattern — we built a working,
> live instance of exactly this architecture, with a synthetic utility called
> **Northwind Grid**, so you can see genuine Fabric capability instead of a
> slide."

Point at the four phases as you say them. That's the whole architecture —
you don't need to explain more yet, the rest of the demo *is* the
explanation.

## Part 2 — Introduce the "world" (2 min)

**Say:**
> "Northwind Grid is a synthetic regional utility — three regions (Coastal,
> Metro, Inland), 12 substations, 36 feeders, 40 generation assets — wind,
> solar, gas peakers, hydro, batteries — and 2,400 smart meters. It's not
> random noise: wind farms follow a real spatial weather front, solar
> follows a sunrise/sunset curve, load follows realistic daily demand shapes.
> Peak demand is roughly 850-950 megawatts against about 1,200 megawatts of
> installed generation — so there's headroom, but not infinite headroom,
> which is exactly what lets us tell an honest stress story instead of a
> rigged one."

**Cheat-sheet of terms you'll use today (say confidently, don't over-explain
unless asked):**
- **MW (megawatt)** — unit of instantaneous power; think "how much the grid
  is producing/using right now."
- **Feeder** — a distribution line segment carrying power from a substation
  out to a group of customers/meters.
- **Substation** — a node where generation and multiple feeders connect.
- **Reserve margin** — spare generation capacity above current demand,
  expressed as a percent; the "safety cushion."
- **Peaker (gas peaker)** — a fast-start gas generator held in reserve for
  peak demand, not run continuously.
- **BESS** — battery energy storage; charges on surplus, discharges on
  shortfall.
- **Deficit / deficit %** — forecasted demand minus forecasted generation,
  as a percent of demand; the core "are we going to be short" metric.

## Part 3 — Show the live pipeline is real (2 min)

Open the Fabric workspace. Walk the item list:

**Say, pointing at each item:**
> "These six Eventstreams are ingesting live telemetry right now — generator
> output, substation/feeder loading, smart meter readings, weather, plus
> customer and asset context. This Eventhouse is a live KQL database. And
> critically — the enrichment and forecasting you're about to see are not
> pre-computed in Python and shipped in. They're native Fabric KQL running
> against this live data, right now, in your tenant."

Optionally run one query live to prove it's not canned:
```kql
grid_telemetry | summarize max(event_time_utc), count()
```
> "That timestamp is seconds old. This is genuinely live."

## Part 4 — Real-Time Dashboard walkthrough (2 min)

Open the Real-Time Dashboard. Walk tile by tile:

- **Grid Overview** — "This is the grid-wide supply/demand balance and
  frequency. Frequency is the grid's heartbeat — 60Hz is healthy; any
  meaningful drift means supply and demand are out of balance."
- **Feeder Loading** — "This is Microsoft's step 10, drill-down from grid to
  feeder level — you can see every feeder's loading percent against its
  rated capacity."
- **Wind Farm Output** — "Wind is the most volatile input, so we watch it
  as its own trend line."
- **Deficit Forecast** — "This is our forward-looking model — not what's
  happening now, but what we expect one and four hours out."
- **Critical Customers on Outage** — "This cross-references outaged feeders
  against customer records to flag anyone tagged critical or medical-need —
  currently empty, which is good news."

## Part 5 — Provoke a real alert, live (4-5 min)

This is the centerpiece. Follow `fabric/PROVOKE_ALERTS.md` to kick off a
stress scenario in a terminal. While it runs:

**Say:**
> "I'm going to push this grid into an evening peak with reduced wind. In
> the real Microsoft example, there are two flagship rules: 'demand exceeds
> 90% of available capacity' — that's a reactive, right-now alert — and
> 'forecast 15% supply shortfall in the evening peak' — that's proactive,
> hours of lead time before it's actually a problem. Watch both fire."

While waiting (~2-4 real minutes), narrate what's happening physically:
> "Wind is dropping at the coast, evening demand is ramping the way real
> household and commercial demand does after work hours, and our reserve
> margin is shrinking in real time on that Grid Overview tile."

When the Teams/email notification lands (or you see it in the Activator run
history):

**Say:**
> "That notification just fired from a live Fabric Activator rule watching
> a native KQL query — not a webhook we built, not a script pretending to
> alert. And notice the wording: 'recommended action, requires approval.'
> Nothing here trips a breaker or sheds load automatically. A human always
> approves."

## Part 6 — Ask the Data Agent, cold (3 min)

Switch to the Data Agent chat. Ask the 5 questions from
`fabric/DATA_AGENT_DEMO_QUESTIONS.md` **without narrating what you expect** —
let the audience watch it reason over live data and answer in plain English.
After each answer, briefly connect it back to what they just saw (e.g. "that
matches the alert we just watched fire").

**Close this section with:**
> "Every one of those answers came from real KQL run against live telemetry,
> grounded — it explicitly refuses to guess when data doesn't support an
> answer, which matters a lot more in a control room than being confidently
> wrong."

## Part 7 — Business view and close (1-2 min)

Switch to the Power BI report (Direct Lake / DirectQuery).

**Say:**
> "This is the same live data, one layer up, for the business side of the
> house — no separate copy, no nightly refresh. And that's the whole
> pattern end to end: ingest, enrich, forecast, visualize, and act — all
> four Microsoft phases, live, in this tenant, with a human in the loop for
> every recommendation."

---

### If asked "is this production-grade?"

**Say:**
> "No — this is a demo utility with synthetic data and simplified physics on
> purpose, so we could show you the full Fabric pattern end-to-end in one
> sitting. The architecture, the schemas, the KQL, and the Activator wiring
> are the same shape you'd use in production; the grid model itself is a
> stand-in, not a certified power-flow solver."

### If asked "does this control real breakers?"

**Say:**
> "Never. Every rule in this system produces a recommendation that a human
> operator approves. There is no closed-loop control anywhere in this
> demo — that's a deliberate design boundary, not a missing feature."
