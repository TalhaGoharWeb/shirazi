"""core/accounts/models.py — user/account data model (Phase 8, SaaS foundation).

Plain dataclasses shared by the account store, session manager, device
registry, provider management, permissions, and usage tracking. No I/O here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


class Role:
    """Account roles. `admin` manages users/providers/permissions and may use
    PRIVILEGED tools (still gated by developer_mode + the human banner).
    `standard` is the everyday user: PRIVILEGED tools are denied outright."""
    ADMIN = "admin"
    STANDARD = "standard"
    ALL = (ADMIN, STANDARD)


@dataclass
class User:
    id: str
    name: str = ""
    role: str = Role.STANDARD
    voice: str = "Charon"
    language: str = "en"          # en | ur | ar | ur-Latn (see i18n/)
    avatar: str = "reactor"       # avatar render style preference
    created_at: float = 0.0
    last_seen: float = 0.0
    paid_opt_in: bool = False     # FREE-FIRST: paid providers need this True
    extra: dict = field(default_factory=dict)

    def is_admin(self) -> bool:
        return self.role == Role.ADMIN


@dataclass
class SessionInfo:
    user_id: str
    kind: str = "bearer"          # bearer | device | api
    device_id: Optional[str] = None
    label: str = ""
    created_at: float = 0.0
    expires_at: float = 0.0
    last_seen: float = 0.0


@dataclass
class DeviceInfo:
    id: str
    user_id: str
    name: str = ""
    kind: str = "phone"           # desktop | phone | tablet | other
    created_at: float = 0.0
    last_seen: float = 0.0
    revoked: bool = False
    session_key: str = ""         # AES session key (server-side only)


@dataclass
class UsageEntry:
    user_id: str
    provider: str
    kind: str                     # requests | tokens_in | tokens_out | tool_calls | errors
    day: str                      # YYYY-MM-DD
    count: int = 0


@dataclass
class ProviderSummary:
    name: str
    tier: str
    enabled: bool
    available: bool
    reason: str = ""
    key_configured: bool = False  # NEVER the key itself
    capabilities: list = field(default_factory=list)


@dataclass
class AutomationRule:
    id: str
    user_id: str
    name: str
    trigger: dict = field(default_factory=dict)
    action: dict = field(default_factory=dict)
    enabled: bool = True
    created_at: float = 0.0


# Usage counter kinds. tokens_in/tokens_out are recorded ONLY when a provider
# implementation reports them — most free-tier REST paths don't, and the
# report says so honestly instead of inventing numbers.
USAGE_KINDS = ("requests", "tokens_in", "tokens_out", "tool_calls", "errors")

# Conversation roles stored in the history table.
CONVERSATION_ROLES = ("user", "assistant", "system", "tool")
