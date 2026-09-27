"""
STEP E - FastAPI control plane + WebSocket stream.

Runs the simulator loop in a background thread, exposes control endpoints,
a live event snapshot, and a WebSocket that fans out every published event.

Run: uvicorn api.main:app --reload --port 8000
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from simulator.engine import World
from simulator.scenarios import NARRATIVE_CARDS, SCENARIOS
from ingest.bus import EventBus
from enrich.join import enrich_generation, enrich_meters, build_erp_index, build_crm_index
from models.forecast import (
    forecast_deficiency, demand_spike_probability, renewable_forecast,
    wind_drop_pct, grid_stability_score,
)
from activate.rules import evaluate_rules
from api.query import answer_query

app = FastAPI(title="Northwind Grid - Real-Time Intelligence Demo")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class SimController:
    """Owns the world + bus + background tick thread. Thread-safe enough for a demo."""

    def __init__(self):
        self.bus = EventBus(replay_buffer_size=1000)
        try:
            from fabric.connectors.eventstream_publisher import maybe_attach_fabric_sink
            maybe_attach_fabric_sink(self.bus)
        except ImportError:
            pass  # azure-eventhub not installed; local-only demo
        self.world: Optional[World] = None
        self.erp_index = {}
        self.crm_index = {}
        self.running = False
        self.speed = 60.0
        self.scenario_id = "S1"
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.alerts: dict[str, dict] = {}  # alert_id -> alert (mutable for approve/dismiss)
        self.last_grid_summary = {}
        self.last_predictions: list[dict] = []
        self._lock = threading.Lock()
        self.reset("S1")

    def reset(self, scenario_id: str, start_hour: Optional[float] = None):
        with self._lock:
            start_local = None
            if start_hour is not None:
                from datetime import datetime, timedelta
                now = datetime.now()
                h = int(start_hour)
                m = int(round((start_hour - h) * 60))
                start_local = now.replace(hour=h, minute=m, second=0, microsecond=0)
                while start_local.weekday() >= 5:
                    start_local += timedelta(days=1)
            self.world = World(scenario_id=scenario_id, speed=self.speed, start_local=start_local)
            self.scenario_id = scenario_id
            erp_rows = self.world.erp_snapshot()
            crm_rows = self.world.crm_snapshot()
            self.erp_index = build_erp_index(erp_rows)
            self.crm_index = build_crm_index(crm_rows)
            self.bus.publish_many("erp_assets", erp_rows)
            self.bus.publish_many("crm_context", crm_rows)
            self.alerts = {}
            self.last_grid_summary = {}
            self.last_predictions = []

    def start(self):
        if self.running:
            return
        self.running = True
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def pause(self):
        self.running = False

    def set_speed(self, speed: float):
        self.speed = speed
        if self.world:
            self.world.clock.speed = speed

    def inject_scenario(self, scenario_id: str, start_hour: Optional[float] = None):
        self.reset(scenario_id, start_hour=start_hour)

    def approve(self, alert_id: str):
        if alert_id in self.alerts:
            self.alerts[alert_id]["status"] = "approved"
            # S7 story: approving a DR-flavoured alert switches on the DR reduction
            if "demand response" in self.alerts[alert_id]["recommended_action"].lower() or \
               self.alerts[alert_id]["rule_id"] in ("RULE-DEMAND-90", "RULE-DEFICIT-15"):
                self.world.dr_active = True
                self.world.dr_start_minute = self.world.minute_of_run
        return self.alerts.get(alert_id)

    def dismiss(self, alert_id: str):
        if alert_id in self.alerts:
            self.alerts[alert_id]["status"] = "dismissed"
        return self.alerts.get(alert_id)

    def snapshot(self) -> dict:
        return {
            "scenario_id": self.scenario_id, "scenario_name": SCENARIOS.get(self.scenario_id),
            "narrative": NARRATIVE_CARDS.get(self.scenario_id),
            "running": self.running, "speed": self.speed,
            "grid_summary": self.last_grid_summary, "predictions": self.last_predictions,
            "alerts": list(self.alerts.values())[-50:],
            "local_time": self.world.clock.iso_local() if self.world else None,
        }

    def topology(self) -> dict:
        return self.world.topology if self.world else {}

    def _loop(self):
        tick_i = 0
        while self.running and not self._stop.is_set():
            world = self.world
            events, grid_summary = world.tick()
            gen_enriched = enrich_generation(events["generation_telemetry"], self.erp_index)
            meter_enriched = enrich_meters(events["meter_telemetry"], self.crm_index)

            self.bus.publish_many("generation_telemetry", gen_enriched)
            self.bus.publish_many("grid_telemetry", events["grid_telemetry"])
            self.bus.publish_many("meter_telemetry", meter_enriched)
            self.bus.publish_many("weather_observations", events["weather_observations"])
            self.last_grid_summary = grid_summary

            predictions = []
            if tick_i % 12 == 0:
                local_hour = world.clock.local_time.hour + world.clock.local_time.minute / 60.0
                avg_temp = sum(o["temp_c"] for o in events["weather_observations"]) / max(1, len(events["weather_observations"]))
                for horizon in (60, 240):
                    d = forecast_deficiency(world.history["grid_summary"], world.feeders, local_hour,
                                             world.is_weekend(), avg_temp, horizon, world.demand_boost)
                    spike_p = demand_spike_probability(local_hour, horizon)
                    pred = {
                        "event_time_utc": world.clock.iso_utc(), "horizon_minutes": horizon,
                        "scope_type": "grid", "scope_id": "GRID-NW",
                        "demand_mw_p50": d["demand_mw_p50"], "generation_mw_p50": d["generation_mw_p50"],
                        "deficit_mw": d["deficit_mw"], "deficit_pct": d["deficit_pct"],
                        "reserve_margin_pct": d["reserve_margin_pct"], "spike_probability": spike_p,
                        "model_version": "heuristic-v1",
                    }
                    predictions.append(pred)
                self.last_predictions = predictions
                self.bus.publish_many("predictions", predictions)

            wind_drops = {farm: wind_drop_pct(world.wind_history, farm, world.farm_nameplate.get(farm, 0.0))
                          for farm in world.wind_history}
            new_alerts = evaluate_rules(grid_summary, predictions, events["grid_telemetry"], meter_enriched,
                                         wind_drops, world.clock.iso_utc(), alert_state=world.gen_alerts_state,
                                         minute_of_run=world.minute_of_run)
            for a in new_alerts:
                self.alerts[a["alert_id"]] = a
            if new_alerts:
                self.bus.publish_many("activator_alerts", new_alerts)

            tick_i += 1
            time.sleep(max(0.05, world.clock.wall_sleep_seconds))


controller = SimController()


class SpeedBody(BaseModel):
    multiplier: float


@app.post("/sim/start")
def sim_start():
    controller.start()
    return {"running": True}


@app.post("/sim/pause")
def sim_pause():
    controller.pause()
    return {"running": False}


@app.post("/sim/speed")
def sim_speed(body: SpeedBody):
    controller.set_speed(body.multiplier)
    return {"speed": controller.speed}


@app.post("/sim/scenario/{scenario_id}")
def sim_scenario(scenario_id: str, start_hour: Optional[float] = None):
    if scenario_id not in SCENARIOS:
        return {"error": f"unknown scenario {scenario_id}"}
    was_running = controller.running
    controller.pause()
    controller.inject_scenario(scenario_id, start_hour=start_hour)
    if was_running:
        controller.start()
    return {"scenario_id": scenario_id, "narrative": NARRATIVE_CARDS.get(scenario_id)}


@app.post("/actions/{alert_id}/approve")
def approve_action(alert_id: str):
    return controller.approve(alert_id) or {"error": "not found"}


@app.post("/actions/{alert_id}/dismiss")
def dismiss_action(alert_id: str):
    return controller.dismiss(alert_id) or {"error": "not found"}


@app.get("/grid/snapshot")
def grid_snapshot():
    return controller.snapshot()


@app.get("/grid/topology")
def grid_topology():
    return controller.topology()


@app.get("/query")
def query(q: str):
    return answer_query(q, controller)


@app.websocket("/stream/events")
async def stream_events(ws: WebSocket):
    await ws.accept()
    loop = asyncio.get_event_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def on_event(stream: str, event: dict):
        try:
            loop.call_soon_threadsafe(queue.put_nowait, {"stream": stream, "event": event})
        except Exception:
            pass

    controller.bus.subscribe(on_event)
    try:
        while True:
            msg = await queue.get()
            await ws.send_text(json.dumps(msg, default=str))
    except WebSocketDisconnect:
        pass
    finally:
        controller.bus.unsubscribe(on_event)
