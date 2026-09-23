"""core/devices.py — the device manager (Phase 4).

Registers audio/input devices and wires config/devices.json. Design rules
from the mission:

  * Enumeration NEVER runs on the UI thread — `refresh_background()` does it
    on a daemon thread and emits events when it finishes.
  * Selection is by device NAME (not index), matching the existing
    core/audio_devices.py convention; names persist in config/devices.json.
  * Paired phones (dashboard) are tracked here too, from the same config
    file's "paired_phones" list.

The actual audio I/O still goes through core/audio_devices.py and main.py's
streams — this manager is the registry + selection + persistence layer, not
a second audio stack.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Optional

from core import events

_BASE = Path(__file__).resolve().parent.parent
_CONFIG_PATH = _BASE / "config" / "devices.json"


class DeviceManager:
    def __init__(self, config_path: Optional[Path] = None):
        self._path = Path(config_path) if config_path else _CONFIG_PATH
        self._lock = threading.RLock()
        self._inputs: list[str] = []
        self._outputs: list[str] = []
        self._input: str = ""
        self._output: str = ""
        self._phones: list[dict] = []
        self._load()

    # ── Config ────────────────────────────────────────────────────────────────
    def _load(self) -> None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        audio = data.get("audio") or {}
        with self._lock:
            self._input = str(audio.get("input_device") or "")
            self._output = str(audio.get("output_device") or "")
            self._phones = list(data.get("paired_phones") or [])

    def _save(self) -> None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        with self._lock:
            data["audio"] = {"input_device": self._input,
                             "output_device": self._output}
            data["paired_phones"] = self._phones
        try:
            self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"[SHIRAZI] ⚠️ devices: could not save config — {e}")

    # ── Enumeration (background only) ─────────────────────────────────────────
    def refresh_background(self) -> threading.Thread:
        """Enumerate devices on a daemon thread. Never call from the UI thread
        expecting it to block — subscribe to device events instead."""
        th = threading.Thread(target=self._refresh, daemon=True,
                              name="shirazi-devices")
        th.start()
        return th

    def _refresh(self) -> None:
        try:
            from core import audio_devices as ad
            inputs = ad.list_devices("input")
            outputs = ad.list_devices("output")
        except Exception as e:
            events.emit("warning", {"where": "devices",
                                    "message": f"enumeration failed: {e}"})
            return
        with self._lock:
            old_in, old_out = set(self._inputs), set(self._outputs)
            self._inputs, self._outputs = list(inputs), list(outputs)
        for name in inputs:
            if name not in old_in:
                events.emit("device.added", {"kind": "input", "name": name})
        for name in outputs:
            if name not in old_out:
                events.emit("device.added", {"kind": "output", "name": name})
        events.emit("system.event",
                    {"message": f"devices: {len(inputs)} in / {len(outputs)} out"})

    # ── Selection ─────────────────────────────────────────────────────────────
    def list_inputs(self) -> list[str]:
        with self._lock:
            return list(self._inputs)

    def list_outputs(self) -> list[str]:
        with self._lock:
            return list(self._outputs)

    def selected_input(self) -> str:
        with self._lock:
            return self._input

    def selected_output(self) -> str:
        with self._lock:
            return self._output

    def select_input(self, name: str) -> bool:
        """Select by name; '' = system default. Persists to devices.json."""
        with self._lock:
            if name and self._inputs and name not in self._inputs:
                return False
            self._input = name
            self._save()
        events.emit("device.selected", {"kind": "input", "name": name})
        return True

    def select_output(self, name: str) -> bool:
        with self._lock:
            if name and self._outputs and name not in self._outputs:
                return False
            self._output = name
            self._save()
        events.emit("device.selected", {"kind": "output", "name": name})
        return True

    # ── Paired phones ─────────────────────────────────────────────────────────
    def paired_phones(self) -> list[dict]:
        with self._lock:
            return [dict(p) for p in self._phones]

    def register_phone(self, device_token: str, label: str = "") -> None:
        with self._lock:
            self._phones = [p for p in self._phones
                            if p.get("device_token") != device_token]
            import time
            self._phones.append({"device_token": device_token,
                                 "label": label,
                                 "paired_at": time.time()})
            self._save()

    def unregister_phone(self, device_token: str) -> bool:
        with self._lock:
            before = len(self._phones)
            self._phones = [p for p in self._phones
                            if p.get("device_token") != device_token]
            self._save()
            return len(self._phones) < before

    # ── Diagnostics ───────────────────────────────────────────────────────────
    def summary(self) -> dict[str, Any]:
        with self._lock:
            return {
                "inputs": list(self._inputs),
                "outputs": list(self._outputs),
                "selected_input": self._input,
                "selected_output": self._output,
                "paired_phones": len(self._phones),
            }
