# Data Agent - 5 "wow" questions for the live demo

Ask these, in order, once the Data Agent instructions in
`FABRIC_SETUP_GUIDE.md` / the agent's own config are in place (it knows
about `AvailableCapacityNow()`, `ForecastGridBalance()`,
`ForecastGridBalanceByRegion()`, `PeakerHeadroomNow()`, and that
`predictions`/`activator_alerts` are unused placeholder tables).

---

## 1. The exact Microsoft reference-architecture question

> **"Show me all regions with predicted energy deficiencies during tomorrow's peak hours."**

Why it's a wow moment: this is verbatim the example question from the
Microsoft Learn architecture page. Answering it live, correctly, off
`ForecastGridBalanceByRegion(240)` -- with real numbers instead of a
canned screenshot -- is the single strongest "we actually built the
official pattern" beat in the whole demo.

## 2. Grid-wide health, one line

> **"What is the current demand as a percentage of available capacity, and is that healthy?"**

Why it's a wow moment: shows the agent doing the exact math behind
`RULE-DEMAND-90` (the 90% alert threshold) on demand, in plain English,
sourced from `AvailableCapacityNow()` -- ties the natural-language box
directly to the Activator rule you already showed firing earlier.

## 3. Feeder-level drill-down

> **"Which feeders are above 80% loading right now, and which region are they in?"**

Why it's a wow moment: demonstrates the grid -> region -> substation ->
feeder drill-down (Microsoft step 10) through conversation instead of
clicking through dashboard tiles -- and if the answer is "none," the
agent now correctly says so as good news instead of hedging.

## 4. Customer-impact / equity angle

> **"Are any critical or medical-baseline customers currently on an outaged feeder?"**

Why it's a wow moment: this is the human-safety angle every utility
executive cares about most. Pulls straight from `crm_context` +
`grid_telemetry`, no manual join needed from the operator -- and reframes
a raw outage list into "who is at risk" in one sentence.

## 5. Dispatch-readiness / operator decision support

> **"What's our available peaker headroom compared to the current reserve margin, and would starting another peaker make sense right now?"**

Why it's a wow moment: this is the "recommend, don't auto-act" story in
one question -- `PeakerHeadroomNow()` + `AvailableCapacityNow()` combine
to give a dispatcher-grade answer, but the agent stops short of
"starting" anything itself. Good moment to say out loud: "notice it
recommends, it doesn't touch a single breaker."

---

### Backup / stretch questions (if you have extra time)

- "Show wind farms whose output dropped more than 20% in the last 15 minutes."
- "Break down current load by region."
- "How much solar generation do we have right now, and what's the cloud cover in each region?"
