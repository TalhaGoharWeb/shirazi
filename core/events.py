"""core/events.py — the lightweight event bus (Phase 4).

UI, audio, tools and providers talk through here instead of importing each
other. Thread-safe; handlers run on the emitter's thread, so a handler that
touches Qt must marshal itself (the UI layer does this already for its own
callbacks — see ui.py's signal usage).

    from core import events
    events.subscribe("speech.speaking", on_speaking)
    events.emit("speech.speaking", {"text": "..."})

Topics are free-form strings, but the WELL_KNOWN list below is the contract
new code should use. Emitting an unknown topic is allowed (prototyping) but
logged once, so typos surface.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Any, Callable, Optional

# The topic contract. Payload shapes are documented per topic.
WELL_KNOWN: dict[str, str] = {
    # Audio / speech
    "speech.listening":   "mic is live and listening; payload {}",
    "speech.speaking":    "assistant started speaking; payload {'text': str}",
    "speech.done":        "assistant finished speaking; payload {}",
    # Cognition
    "thinking.start":     "a model call began; payload {'provider': str, 'why': str}",
    "thinking.done":      "a model call returned; payload {'provider': str, 'ms': int}",
    # Tools
    "tool.called":        "a tool was invoked; payload {'name': str, 'permission': str}",
    "tool.confirmed":     "user confirmed a gated tool; payload {'name': str}",
    "tool.denied":        "permission engine denied a tool; payload {'name': str, 'reason': str}",
    "tool.completed":     "a tool finished; payload {'name': str, 'ok': bool}",
    # Data
    "search.start":       "a web search began; payload {'query': str}",
    "search.done":        "a web search finished; payload {'query': str, 'hits': int}",
    "file.operation":     "a file op ran; payload {'op': str, 'path': str, 'ok': bool}",
    # System / devices
    "system.event":       "generic system note; payload {'message': str}",
    "device.added":       "an audio/input device appeared; payload {'kind': str, 'name': str}",
    "device.removed":     "a device disappeared; payload {'kind': str, 'name': str}",
    "device.selected":    "default device changed; payload {'kind': str, 'name': str}",
    # Assistant state
    "state.changed":      "UI state changed; payload {'state': str} e.g. LISTENING",
    "memory.changed":     "a memory layer changed; payload {'layer': str, 'key': str}",
    # Agent engine (Phase 6)
    "agent.plan":         "the agent built a plan; payload {'request': str, 'intent': str, 'steps': [str]}",
    "agent.step":         "an agent plan step finished; payload {'n': int, 'tool': str, 'ok': bool}",
    "agent.done":         "an agent run finished; payload {'status': str, 'steps': int}",
    # Problems
    "warning":            "non-fatal; payload {'where': str, 'message': str}",
    "error":              "fatal-ish; payload {'where': str, 'message': str}",
}

_lock = threading.RLock()
_subscribers: dict[str, list[Callable[[dict], None]]] = defaultdict(list)
_warned_topics: set[str] = set()
_stats: dict[str, int] = defaultdict(int)


def subscribe(topic: str, handler: Callable[[dict], None]) -> Callable[[], None]:
    """Subscribe; returns an unsubscribe callable."""
    with _lock:
        _subscribers[topic].append(handler)

    def _unsubscribe() -> None:
        unsubscribe(topic, handler)

    return _unsubscribe


def unsubscribe(topic: str, handler: Callable[[dict], None]) -> None:
    with _lock:
        try:
            _subscribers[topic].remove(handler)
        except ValueError:
            pass


def emit(topic: str, payload: Optional[dict] = None) -> int:
    """Emit to all subscribers. Returns the number of handlers called.

    A handler that raises never breaks the emit — the error is counted and
    the rest still run. Emits never raise.
    """
    data = dict(payload or {})
    data.setdefault("topic", topic)
    data.setdefault("ts", time.time())
    with _lock:
        handlers = list(_subscribers.get(topic, []))
        _stats[topic] += 1
        if topic not in WELL_KNOWN and topic not in _warned_topics:
            _warned_topics.add(topic)
            print(f"[SHIRAZI] ⚠️ events: unknown topic '{topic}' "
                  f"(allowed, but not in the contract)")
    delivered = 0
    for handler in handlers:
        try:
            handler(data)
            delivered += 1
        except Exception as e:  # noqa: BLE001 — one bad handler ≠ a dead bus
            print(f"[SHIRAZI] ⚠️ events: handler for '{topic}' raised: {e}")
    return delivered


def subscriber_count(topic: str) -> int:
    with _lock:
        return len(_subscribers.get(topic, []))


def stats() -> dict[str, int]:
    """Emit counts per topic (diagnostics)."""
    with _lock:
        return dict(_stats)


def reset() -> None:
    """Clear all subscriptions and stats (tests)."""
    with _lock:
        _subscribers.clear()
        _warned_topics.clear()
        _stats.clear()
