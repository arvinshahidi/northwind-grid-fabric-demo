"""
Local event bus - a stand-in for Fabric Eventstream. In-process pub/sub with
an optional ndjson sink. The API layer subscribes to this for the WS stream;
the CLI simulator subscribes an ndjson writer.
"""
from __future__ import annotations

import json
import threading
from collections import deque
from typing import Callable


class EventBus:
    def __init__(self, replay_buffer_size: int = 500):
        self._subscribers: list[Callable[[str, dict], None]] = []
        self._lock = threading.Lock()
        self.replay: dict[str, deque] = {}
        self.replay_buffer_size = replay_buffer_size

    def subscribe(self, fn: Callable[[str, dict], None]):
        with self._lock:
            self._subscribers.append(fn)

    def unsubscribe(self, fn: Callable[[str, dict], None]):
        with self._lock:
            if fn in self._subscribers:
                self._subscribers.remove(fn)

    def publish(self, stream: str, event: dict):
        buf = self.replay.setdefault(stream, deque(maxlen=self.replay_buffer_size))
        buf.append(event)
        with self._lock:
            subs = list(self._subscribers)
        for fn in subs:
            try:
                fn(stream, event)
            except Exception:
                pass

    def publish_many(self, stream: str, events: list[dict]):
        for e in events:
            self.publish(stream, e)


class NdjsonSink:
    """Subscriber that appends every event to a per-stream ndjson file."""

    def __init__(self, out_dir: str):
        import os
        os.makedirs(out_dir, exist_ok=True)
        self.out_dir = out_dir
        self._handles = {}

    def __call__(self, stream: str, event: dict):
        import os
        if stream not in self._handles:
            self._handles[stream] = open(os.path.join(self.out_dir, f"{stream}.ndjson"), "a", encoding="utf-8")
        self._handles[stream].write(json.dumps(event, default=str) + "\n")

    def close(self):
        for h in self._handles.values():
            h.close()
