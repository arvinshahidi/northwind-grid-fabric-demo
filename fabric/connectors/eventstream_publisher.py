"""
Real Fabric Eventstream publisher.

Fabric Eventstream's "Custom App" source type hands you an Event Hub
compatible connection string. This module is an EventBus subscriber
(same shape as ingest.bus.NdjsonSink) that batches events per stream and
sends them to the matching Eventstream, using the standard Azure Event Hubs
SDK -- no Fabric-specific SDK needed.

Usage
-----
1. In the Fabric portal, create one Eventstream per data stream you want
   live (start with generation_telemetry, grid_telemetry, meter_telemetry,
   predictions, activator_alerts -- see FABRIC_SETUP_GUIDE.md Day 2).
2. Add a "Custom App" source to each Eventstream; copy its connection
   string (it looks like an Event Hubs connection string with
   EntityPath=<eventstream-name>).
3. Put those connection strings in environment variables named
   FABRIC_ES_<STREAM_NAME_UPPER>, e.g.:
     FABRIC_ES_GENERATION_TELEMETRY="Endpoint=sb://...;SharedAccessKeyName=...;SharedAccessKey=...;EntityPath=es-generation-telemetry"
4. Wire it into the bus (CLI or API) with:
     from fabric.connectors.eventstream_publisher import FabricEventstreamSink
     sink = FabricEventstreamSink()
     bus.subscribe(sink)
   Streams with no configured connection string are silently skipped, so you
   can wire streams one at a time without breaking the local demo.

This is additive: the local ndjson/WebSocket path keeps working unchanged.
Requires: pip install azure-eventhub  (see requirements-fabric.txt)
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Optional

logger = logging.getLogger("fabric.eventstream_publisher")


def _true_utc_now() -> datetime:
    """Best-effort accurate real-world UTC "now".

    Some sandboxed/dev environments run with a system clock that has
    drifted from true wall-clock time (observed: several days off). Since
    Fabric's own ago()/now() KQL functions evaluate against the ACTUAL
    current time on Microsoft's servers, any local clock drift here would
    silently corrupt real-time-stamping and backfill-window math -- events
    could land looking days in the future or past relative to Fabric.

    This does a quick HEAD request to a well-known HTTPS endpoint and reads
    the standard `Date` response header (present on effectively all HTTP
    servers) as a free, dependency-free source of truth. Falls back to the
    local system clock if the network check fails for any reason (offline
    demo, firewall, etc.) -- never raises.
    """
    for url in ("https://www.microsoft.com", "https://www.google.com"):
        try:
            import urllib.request
            req = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(req, timeout=3) as resp:
                date_header = resp.headers.get("Date")
                if date_header:
                    dt = parsedate_to_datetime(date_header)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return dt.astimezone(timezone.utc)
        except Exception:
            continue
    logger.warning("Could not verify true UTC time over the network; falling back to local system clock, "
                   "which may be drifted in some sandboxed environments.")
    return datetime.now(timezone.utc)

# Local demo timezone offset used elsewhere in the simulator (Pacific).
_LOCAL_TZ_OFFSET_HOURS = -7

# Which field(s) on each event carry the timestamp that must be re-stamped
# to real wall-clock time before forwarding to Fabric. The simulator's
# internal clock runs at `speed`x acceleration purely for physics/pacing;
# Fabric's native ago()/now()-based KQL functions (series_decompose_forecast,
# update policies, dashboards) need event_time_utc to track real time as
# events actually arrive, or a multi-hour simulated run finishing in a few
# real minutes would look like it's "from the future".
_TIME_FIELDS = {
    "generation_telemetry": ("event_time_utc", "event_time_local"),
    "grid_telemetry": ("event_time_utc", "event_time_local"),
    "meter_telemetry": ("event_time_utc", "event_time_local"),
    "crm_context": ("event_time_utc",),
    "weather_observations": ("event_time_utc",),
    "predictions": ("event_time_utc",),
    "activator_alerts": ("event_time_utc",),
    "erp_assets": ("as_of_utc",),
}

# Streams this connector knows how to route. Matches schemas/models.py stream names.
KNOWN_STREAMS = [
    "generation_telemetry", "grid_telemetry", "meter_telemetry", "crm_context",
    "erp_assets", "weather_observations", "predictions", "activator_alerts",
]


def _env_var_for_stream(stream: str) -> str:
    return f"FABRIC_ES_{stream.upper()}"


class FabricEventstreamSink:
    """EventBus subscriber that forwards events to real Fabric Eventstreams.

    Batches events per stream and flushes on a background timer (default
    every 2 seconds) or once a batch reaches `max_batch` events, to avoid
    one Event Hubs send call per 5-second simulator tick per stream.
    """

    def __init__(self, flush_interval_s: float = 2.0, max_batch: int = 200,
                 max_buffer: int = 4000, streams: Optional[list[str]] = None,
                 sample_rate: Optional[dict[str, float]] = None,
                 realtime_stamp: bool = True,
                 backfill_minutes: Optional[float] = None):
        try:
            from azure.eventhub import EventHubProducerClient, EventData  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "azure-eventhub is required for FabricEventstreamSink. "
                "Install with: pip install -r requirements-fabric.txt"
            ) from exc
        self._EventHubProducerClient = EventHubProducerClient
        self._EventData = EventData

        # Compute the local-clock-vs-true-time drift ONCE (a single network
        # check), then use a cheap local-clock read + cached offset for
        # every event afterward. Never do network I/O per event -- that
        # would reintroduce the exact blocking problem the threaded design
        # fixes.
        self._clock_offset = _true_utc_now() - datetime.now(timezone.utc)
        if abs(self._clock_offset) > timedelta(minutes=5):
            logger.warning(
                "Local system clock appears drifted from true UTC by %s; "
                "correcting all Fabric-bound event timestamps by this offset.",
                self._clock_offset,
            )

        self.flush_interval_s = flush_interval_s
        self.max_batch = max_batch
        # Backfill mode: map a whole (fast, accelerated) simulator run onto a
        # real-time window ending approximately "now" -- so a run started
        # with e.g. --minutes 360 lands as if it had been streaming live for
        # the past 6 real hours. This is what makes ago()/now()-based KQL
        # forecast functions usable immediately after a seeding run, instead
        # of requiring the demo to literally run for 6 real hours first. Set
        # backfill_minutes ~= the --minutes value of the run for a clean map.
        self.backfill_minutes = backfill_minutes
        self._backfill_anchor_real = self._now() if backfill_minutes else None
        self._backfill_total = timedelta(minutes=backfill_minutes) if backfill_minutes else None
        self._sim_first_ts: dict[str, datetime] = {}
        # Re-stamp event_time_utc/event_time_local/as_of_utc to the real
        # wall-clock time at forwarding time, so Fabric's native ago()/now()
        # KQL functions see a genuinely live stream regardless of the
        # simulator's internal acceleration (`speed`). Disable only for
        # niche cases where you want Fabric to see the simulator's own
        # (possibly future-dated or accelerated) clock verbatim.
        self.realtime_stamp = realtime_stamp
        # Hard cap per stream buffer -- if the network can't keep up with the
        # simulator, we drop the OLDEST buffered events rather than block the
        # simulation loop or grow memory unbounded. This is a demo relay, not
        # a durable queue; Fabric itself is the system of record once events
        # land.
        self.max_buffer = max_buffer
        # Optional per-stream sampling (0 < rate <= 1) to cap high-volume
        # streams like meter_telemetry (2,400 meters/tick) to a demo-friendly
        # rate without falling behind real time. Defaults below can be
        # overridden via the `sample_rate` argument or FABRIC_ES_SAMPLE_<STREAM>.
        self.sample_rate = {
            "meter_telemetry": 0.1,
        }
        if sample_rate:
            self.sample_rate.update(sample_rate)
        for stream in KNOWN_STREAMS:
            env_key = f"FABRIC_ES_SAMPLE_{stream.upper()}"
            if os.environ.get(env_key):
                try:
                    self.sample_rate[stream] = float(os.environ[env_key])
                except ValueError:
                    pass

        self._buffers: dict[str, list[dict]] = defaultdict(list)
        self._lock = threading.Lock()
        self._producers: dict[str, "EventHubProducerClient"] = {}
        self._configured_streams: set[str] = set()
        self._sent_counts: dict[str, int] = defaultdict(int)
        self._dropped_counts: dict[str, int] = defaultdict(int)
        self._sample_counter: dict[str, int] = defaultdict(int)

        candidate_streams = streams or KNOWN_STREAMS
        for stream in candidate_streams:
            conn_str = os.environ.get(_env_var_for_stream(stream))
            if not conn_str:
                continue
            try:
                self._producers[stream] = EventHubProducerClient.from_connection_string(conn_str)
                self._configured_streams.add(stream)
                logger.info("Fabric Eventstream sink: wired stream '%s'", stream)
            except Exception:
                logger.exception("Failed to create producer for stream '%s'", stream)

        if not self._configured_streams:
            logger.warning(
                "FabricEventstreamSink created but no FABRIC_ES_* connection strings "
                "found in the environment -- events will not be forwarded to Fabric."
            )

        # One background flush thread PER configured stream so a slow/blocked
        # send on one stream (e.g. network hiccup) never delays another
        # stream's flush, and -- critically -- neither ever runs on the main
        # simulation thread.
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        for stream in self._configured_streams:
            t = threading.Thread(target=self._flush_loop, args=(stream,), daemon=True)
            t.start()
            self._threads.append(t)

    def _now(self) -> datetime:
        """Cheap, drift-corrected 'true' UTC now (no per-call network I/O)."""
        return datetime.now(timezone.utc) + self._clock_offset

    def configured_streams(self) -> list[str]:
        return sorted(self._configured_streams)

    def stats(self) -> dict[str, dict[str, int]]:
        """Sent/dropped counters per stream, useful for a live 'events/sec' UI."""
        return {
            stream: {"sent": self._sent_counts[stream], "dropped": self._dropped_counts[stream]}
            for stream in self._configured_streams
        }

    def __call__(self, stream: str, event: dict):
        if stream not in self._configured_streams:
            return
        rate = self.sample_rate.get(stream)
        if rate is not None and rate < 1.0:
            self._sample_counter[stream] += 1
            # Deterministic decimation (keep every Nth event) rather than
            # random sampling, so behaviour is reproducible run to run.
            if (self._sample_counter[stream] % max(1, round(1 / rate))) != 0:
                return
        if self.backfill_minutes:
            event = self._backfill_stamp(stream, event)
        elif self.realtime_stamp:
            event = self._restamp(stream, event)
        # Never send network I/O on this thread (the simulator's main loop) --
        # only buffer. A background per-stream thread does all sending.
        with self._lock:
            buf = self._buffers[stream]
            buf.append(event)
            if len(buf) > self.max_buffer:
                dropped = len(buf) - self.max_buffer
                del buf[:dropped]
                self._dropped_counts[stream] += dropped

    def _backfill_stamp(self, stream: str, event: dict) -> dict:
        """Map this event's simulator timestamp onto a real-time window that
        ends approximately now, so a fast batch run looks like it streamed
        live over the past `backfill_minutes` real minutes.
        """
        fields = _TIME_FIELDS.get(stream)
        if not fields:
            return event
        source_field = "event_time_utc" if "event_time_utc" in event else fields[0]
        raw = event.get(source_field)
        if not raw:
            return event
        try:
            orig_dt = datetime.fromisoformat(raw)
        except ValueError:
            return event
        first_dt = self._sim_first_ts.setdefault(stream, orig_dt)
        elapsed = orig_dt - first_dt
        target_utc = self._backfill_anchor_real - self._backfill_total + elapsed
        target_local = target_utc + timedelta(hours=_LOCAL_TZ_OFFSET_HOURS)
        restamped = dict(event)
        for field in fields:
            restamped[field] = (target_local if field == "event_time_local" else target_utc).isoformat()
        return restamped

    def _restamp(self, stream: str, event: dict) -> dict:
        """Return a shallow copy of `event` with its timestamp field(s) set to
        the real wall-clock time now, leaving all physics/telemetry values
        (from the simulator's accelerated internal clock) untouched. This is
        what makes ago()/now()-based KQL functions in Fabric treat the feed
        as genuinely live, no matter how fast --speed runs the simulator.
        """
        fields = _TIME_FIELDS.get(stream)
        if not fields:
            return event
        now_utc = self._now()
        now_local = now_utc + timedelta(hours=_LOCAL_TZ_OFFSET_HOURS)
        restamped = dict(event)
        for field in fields:
            if field in ("event_time_local",):
                restamped[field] = now_local.isoformat()
            else:
                restamped[field] = now_utc.isoformat()
        return restamped

    def _flush_loop(self, stream: str):
        while not self._stop.is_set():
            time.sleep(self.flush_interval_s)
            self._flush_stream(stream)

    def _flush_stream(self, stream: str):
        with self._lock:
            batch_events = self._buffers[stream]
            self._buffers[stream] = []
        if not batch_events:
            return
        producer = self._producers.get(stream)
        if not producer:
            return
        try:
            batch = producer.create_batch()
            sent = 0
            for event in batch_events:
                data = self._EventData(json.dumps(event, default=str))
                try:
                    batch.add(data)
                except ValueError:
                    # batch full: send what we have, start a new one
                    producer.send_batch(batch)
                    sent += len(batch)
                    batch = producer.create_batch()
                    batch.add(data)
            producer.send_batch(batch)
            sent += len(batch)
            self._sent_counts[stream] += len(batch_events)
        except Exception:
            logger.exception("Failed to send batch of %d events for stream '%s'", len(batch_events), stream)

    def close(self):
        self._stop.set()
        for stream in list(self._configured_streams):
            self._flush_stream(stream)
        for producer in self._producers.values():
            try:
                producer.close()
            except Exception:
                pass


def maybe_attach_fabric_sink(bus, backfill_minutes: Optional[float] = None,
                              run_minutes_hint: Optional[float] = None) -> Optional["FabricEventstreamSink"]:
    """Best-effort helper for the CLI/API entry points.

    Returns a FabricEventstreamSink subscribed to `bus` if at least one
    FABRIC_ES_* environment variable is configured and azure-eventhub is
    installed; otherwise returns None and leaves the local demo untouched.
    Never raises -- a misconfigured or absent Fabric connection must not
    break the local demo.

    Backfill mode maps a fast/accelerated simulator run onto a real-time
    window ending now, so KQL forecast functions that need real history
    (ago()/now()) have something to work with right after a seeding run,
    without literally waiting hours. It is OFF by default (plain live runs
    real-time-stamp every event with actual wall-clock "now"). Opt in with
    either:
      - `backfill_minutes=<n>` explicitly, or
      - env var FABRIC_BACKFILL_MINUTES=<n>, or
      - env var FABRIC_BACKFILL_MINUTES=auto (uses `run_minutes_hint`, i.e.
        this run's own --minutes value, for a clean 1:1 map)
    """
    if not any(os.environ.get(_env_var_for_stream(s)) for s in KNOWN_STREAMS):
        return None
    if backfill_minutes is None:
        env_val = os.environ.get("FABRIC_BACKFILL_MINUTES")
        if env_val and env_val.strip().lower() == "auto":
            backfill_minutes = run_minutes_hint
        elif env_val:
            try:
                backfill_minutes = float(env_val)
            except ValueError:
                backfill_minutes = None
    realtime_stamp = os.environ.get("FABRIC_REALTIME_STAMP", "true").strip().lower() not in ("0", "false", "no")
    try:
        sink = FabricEventstreamSink(realtime_stamp=realtime_stamp, backfill_minutes=backfill_minutes)
    except Exception:
        logger.exception("Could not start FabricEventstreamSink; continuing with local demo only")
        return None
    if not sink.configured_streams():
        return None
    bus.subscribe(sink)
    if backfill_minutes:
        stamp_note = f"backfilled over the past {backfill_minutes:.0f} real minutes"
    else:
        stamp_note = "real wall-clock timestamps" if realtime_stamp else "simulator's own (accelerated) timestamps"
    print(f"[fabric] live-forwarding streams to real Fabric Eventstreams "
          f"({stamp_note}): {', '.join(sink.configured_streams())}")
    return sink
