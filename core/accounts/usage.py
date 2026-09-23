"""core/accounts/usage.py — per-user/per-provider usage tracking (Phase 8).

SQLite daily counters in the accounts db:

    record(user_id, provider, kind, amount=1)
    report(user_id, days=30)   → {provider: {kind: count, ...}, ...}
    totals(user_id, days=30)   → {kind: count}
    reset(user_id, ...)        → admin/testing helper

Kinds: requests | tokens_in | tokens_out | tool_calls | errors.

HONESTY (documented, not faked):
- "requests" counts provider *attempts* made through
  core/providers/registry.generate() — the chain leg actually tried.
- "errors" counts ProviderError failures per provider.
- "tokens_in"/"tokens_out" are recorded ONLY when a provider
  implementation reports token counts. Today NO built-in provider reports
  them (free-tier REST responses in core/gemini.py and openrouter.py don't
  surface usage reliably), so those columns stay 0 and the report carries
  an explicit note saying so — instead of inventing numbers.
- The Live voice session (main.py ShiraziLive) does NOT route through the
  registry, so voice-session usage is not counted here. The report states
  this. Counting it would require instrumenting the Live loop — a Phase 9
  candidate.

Wiring: dashboard/server.py installs the recorder with
install_registry_recorder(store, user_id) so registry-routed calls count
automatically. The recorder never raises and never blocks generation.
"""

from __future__ import annotations

import datetime
import threading
import time
from typing import Optional

from .models import USAGE_KINDS
from .store import AccountStore

_lock = threading.RLock()


class UsageTracker:
    def __init__(self, store: AccountStore):
        self._store = store

    def record(self, user_id: str, provider: str, kind: str,
               amount: int = 1) -> None:
        """Increment a counter. Never raises (tracking must not break the
        assistant)."""
        if kind not in USAGE_KINDS:
            return
        try:
            amount = int(amount)
        except Exception:
            return
        if amount <= 0:
            return
        day = datetime.date.today().isoformat()
        try:
            with _lock, self._store._connect() as conn:
                conn.execute(
                    "INSERT INTO usage(user_id, provider, kind, day, count)"
                    " VALUES(?,?,?,?,?)"
                    " ON CONFLICT(user_id, provider, kind, day) DO UPDATE"
                    " SET count = count + excluded.count",
                    (str(user_id), str(provider), kind, day, amount))
        except Exception:
            pass  # tracking is best-effort

    def report(self, user_id: str, days: int = 30) -> dict:
        """{provider: {kind: count}}, plus honesty notes."""
        since = (datetime.date.today()
                 - datetime.timedelta(days=max(1, int(days)))).isoformat()
        rows: list = []
        try:
            with _lock, self._store._connect() as conn:
                rows = conn.execute(
                    "SELECT provider, kind, SUM(count) AS total FROM usage"
                    " WHERE user_id=? AND day >= ?"
                    " GROUP BY provider, kind",
                    (str(user_id), since)).fetchall()
        except Exception:
            pass
        out: dict[str, dict[str, int]] = {}
        for r in rows:
            out.setdefault(r["provider"], {})[r["kind"]] = int(r["total"])
        # fill missing kinds with 0 so the shape is stable
        for prov in out:
            for kind in USAGE_KINDS:
                out[prov].setdefault(kind, 0)
        return {
            "user_id": str(user_id),
            "days": int(days),
            "providers": out,
            "notes": [
                "requests = provider attempts via core/providers/registry.generate().",
                "tokens_in/tokens_out are 0: no provider implementation "
                "reports token counts today (not estimated).",
                "Voice (Gemini Live) sessions do not route through the "
                "registry and are not counted here.",
            ],
        }

    def totals(self, user_id: str, days: int = 30) -> dict[str, int]:
        rep = self.report(user_id, days=days)
        totals = {k: 0 for k in USAGE_KINDS}
        for prov in rep["providers"].values():
            for k in USAGE_KINDS:
                totals[k] += prov.get(k, 0)
        return totals

    def reset(self, user_id: str, provider: Optional[str] = None) -> int:
        """Delete usage rows (admin/testing). Returns rows removed."""
        try:
            with _lock, self._store._connect() as conn:
                if provider:
                    cur = conn.execute(
                        "DELETE FROM usage WHERE user_id=? AND provider=?",
                        (str(user_id), str(provider)))
                else:
                    cur = conn.execute(
                        "DELETE FROM usage WHERE user_id=?", (str(user_id),))
                return cur.rowcount
        except Exception:
            return 0


def install_registry_recorder(store: AccountStore,
                              user_id: str = "local") -> None:
    """Hook usage tracking into the provider chain: every registry-routed
    attempt records requests/errors under `user_id`. Safe to call once at
    boot; the recorder never raises into generate()."""
    from core.providers import registry
    tracker = UsageTracker(store)
    uid = str(user_id)

    def _recorder(rid: str, provider: str, kind: str, amount: int = 1):
        if rid != uid:
            # Multi-user call paths pass their own user_id through
            # registry.generate(user_id=...); anything else attributes to
            # the recorder's default user.
            target = rid or uid
        else:
            target = uid
        tracker.record(target, provider, kind, amount)

    registry.set_usage_recorder(_recorder)
