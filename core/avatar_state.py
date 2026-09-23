"""core/avatar_state.py — the avatar/HUD state machine (Phase 5).

The assistant's visible state drives the avatar animation, the reactor
visualisation and the HUD status line. It is a strict machine, not a bag of
booleans: every transition is declared, illegal ones are rejected loudly in
tests and logged-and-ignored at runtime (the UI must never crash because an
audio thread reported SPEAKING while the machine was OFFLINE).

States (one is active at a time):
    IDLE        — awake, waiting. Gentle breathing, blinking.
    LISTENING   — mic live, user talking. Waveform reacts to mic level.
    THINKING    — a model call is in flight. Processing shimmer.
    SPEAKING    — assistant voice playing. Mouth articulates visemes.
    EXECUTING   — a tool is running. Focused pulse.
    SUCCESS     — brief confirmation flash after a completed tool.
    ERROR       — something failed. Amber/red, restrained — not alarming.
    OFFLINE     — disconnected / asleep. Dim, slow breathing ("sleep state").

The machine is deliberately UI-agnostic: it emits core.events "state.changed"
and stores the current state; the Qt layer (ui.py HudCanvas) subscribes and
maps states to colours/animations. Headless tests can drive it directly.
"""

from __future__ import annotations

import threading
from enum import Enum

from core import events


class AvatarState(str, Enum):
    IDLE       = "IDLE"
    LISTENING  = "LISTENING"
    THINKING   = "THINKING"
    SPEAKING   = "SPEAKING"
    EXECUTING  = "EXECUTING"
    SUCCESS    = "SUCCESS"
    ERROR      = "ERROR"
    OFFLINE    = "OFFLINE"


# Legal transitions. SUCCESS and ERROR are transient confirmations that always
# settle back to IDLE; OFFLINE is reachable from anywhere and leaves to IDLE.
_TRANSITIONS: dict[AvatarState, frozenset[AvatarState]] = {
    AvatarState.IDLE:      frozenset({AvatarState.LISTENING, AvatarState.THINKING,
                                      AvatarState.SPEAKING, AvatarState.EXECUTING,
                                      AvatarState.SUCCESS, AvatarState.ERROR,
                                      AvatarState.OFFLINE}),
    AvatarState.LISTENING: frozenset({AvatarState.IDLE, AvatarState.THINKING,
                                      AvatarState.EXECUTING, AvatarState.ERROR,
                                      AvatarState.OFFLINE}),
    AvatarState.THINKING:  frozenset({AvatarState.SPEAKING, AvatarState.EXECUTING,
                                      AvatarState.IDLE, AvatarState.ERROR,
                                      AvatarState.OFFLINE}),
    AvatarState.SPEAKING:  frozenset({AvatarState.IDLE, AvatarState.LISTENING,
                                      AvatarState.THINKING, AvatarState.EXECUTING,
                                      AvatarState.SUCCESS, AvatarState.ERROR,
                                      AvatarState.OFFLINE}),
    AvatarState.EXECUTING: frozenset({AvatarState.IDLE, AvatarState.SPEAKING,
                                      AvatarState.SUCCESS, AvatarState.ERROR,
                                      AvatarState.OFFLINE}),
    AvatarState.SUCCESS:   frozenset({AvatarState.IDLE, AvatarState.LISTENING,
                                      AvatarState.THINKING, AvatarState.SPEAKING,
                                      AvatarState.EXECUTING}),
    AvatarState.ERROR:     frozenset({AvatarState.IDLE, AvatarState.LISTENING,
                                      AvatarState.THINKING, AvatarState.SPEAKING,
                                      AvatarState.EXECUTING, AvatarState.OFFLINE}),
    AvatarState.OFFLINE:   frozenset({AvatarState.IDLE}),
}


class IllegalTransition(ValueError):
    """Raised by `transition()` on a transition the machine does not allow."""


class AvatarStateMachine:
    """Thread-safe avatar state machine.

    `transition(state)` validates and applies; `request(state)` validates and
    applies but returns False (and logs) instead of raising on illegal moves —
    use it from audio/network threads where raising is worse than ignoring.
    Both emit the "state.changed" event on a real change.
    """

    def __init__(self, initial: AvatarState = AvatarState.IDLE):
        self._lock = threading.RLock()
        self._state = initial
        self._history: list[AvatarState] = [initial]

    @property
    def state(self) -> AvatarState:
        with self._lock:
            return self._state

    def can(self, target: AvatarState) -> bool:
        with self._lock:
            return target in _TRANSITIONS[self._state]

    def transition(self, target: AvatarState) -> AvatarState:
        """Move to `target`; raises IllegalTransition on a forbidden move."""
        with self._lock:
            if target not in _TRANSITIONS[self._state]:
                raise IllegalTransition(
                    f"{self._state.value} -> {target.value} is not a legal "
                    f"avatar transition")
            previous = self._state
            if target is not previous:
                self._state = target
                self._history.append(target)
        if target is not previous:
            events.emit("state.changed",
                        {"state": target.value, "previous": previous.value})
        return target

    def request(self, target: AvatarState) -> bool:
        """Like transition(), but returns False instead of raising.

        For threads that must never crash the app (audio callbacks, network
        handlers): an illegal request is logged and dropped.
        """
        try:
            self.transition(target)
            return True
        except IllegalTransition as e:
            events.emit("warning",
                        {"where": "avatar_state", "message": str(e)})
            return False

    def history(self, n: int = 10) -> list[str]:
        with self._lock:
            return [s.value for s in self._history[-n:]]


# ── Animation intents ────────────────────────────────────────────────────────
# The Qt layer maps each state to a small set of animation intents. Kept here
# (not in ui.py) so the mapping is testable and shared by every renderer
# (avatar head, hologram, reactor core).

#: Per-state animation parameters consumed by the renderers. Values are
#: normalised: rate in Hz, amp 0..1, glow 0..1 multiplier.
STATE_ANIMATION: dict[AvatarState, dict] = {
    AvatarState.IDLE:      {"breath_hz": 0.25, "breath_amp": 0.25,
                            "head_sway": 0.30, "blink": True,
                            "glow": 0.55, "motion": "idle"},
    AvatarState.LISTENING: {"breath_hz": 0.45, "breath_amp": 0.35,
                            "head_sway": 0.55, "blink": True,
                            "glow": 0.80, "motion": "listening"},
    AvatarState.THINKING:  {"breath_hz": 0.90, "breath_amp": 0.20,
                            "head_sway": 0.15, "blink": False,
                            "glow": 0.70, "motion": "thinking"},
    AvatarState.SPEAKING:  {"breath_hz": 0.35, "breath_amp": 0.30,
                            "head_sway": 0.45, "blink": True,
                            "glow": 0.95, "motion": "speaking"},
    AvatarState.EXECUTING: {"breath_hz": 0.60, "breath_amp": 0.22,
                            "head_sway": 0.10, "blink": False,
                            "glow": 0.75, "motion": "processing"},
    AvatarState.SUCCESS:   {"breath_hz": 0.40, "breath_amp": 0.30,
                            "head_sway": 0.20, "blink": True,
                            "glow": 1.00, "motion": "success"},
    AvatarState.ERROR:     {"breath_hz": 0.20, "breath_amp": 0.20,
                            "head_sway": 0.05, "blink": False,
                            "glow": 0.60, "motion": "error"},
    AvatarState.OFFLINE:   {"breath_hz": 0.12, "breath_amp": 0.15,
                            "head_sway": 0.00, "blink": False,
                            "glow": 0.18, "motion": "sleep"},
}


def animation_for(state: AvatarState) -> dict:
    """The animation intent dict for a state (a copy; mutate freely)."""
    return dict(STATE_ANIMATION[state])
