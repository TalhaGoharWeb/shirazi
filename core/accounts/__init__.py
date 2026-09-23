"""core/accounts/ — SaaS foundation: user accounts, sessions, devices,
provider management, per-user permissions, usage tracking, encrypted
secrets (Phase 8).

The dashboard's bearer-token auth is the single-user instance of the
general mechanism here: DashboardServer issues sessions for the "local"
admin user through SessionManager.

Import surface:
    from core.accounts import (AccountStore, SessionManager, SecretStore,
                               UsageTracker, ensure_local_user, models,
                               sessions, devices, usage, secrets, permissions)
    import core.accounts.providers   # explicit (pulls the provider stack)

Design-only vs working — see docs/SAAS.md for the honest breakdown.
"""

from .models import Role, User, SessionInfo, DeviceInfo, UsageEntry  # noqa: F401
from .models import ProviderSummary, AutomationRule  # noqa: F401
from .store import AccountStore, ensure_local_user  # noqa: F401
from .sessions import SessionManager  # noqa: F401
from . import devices  # noqa: F401
from . import usage  # noqa: F401
from . import secrets  # noqa: F401
from . import permissions as user_permissions  # noqa: F401
from .usage import UsageTracker  # noqa: F401
from .secrets import SecretStore  # noqa: F401

__all__ = [
    "AccountStore", "SessionManager", "SecretStore", "UsageTracker",
    "ensure_local_user", "Role", "User", "SessionInfo", "DeviceInfo",
    "UsageEntry", "ProviderSummary", "AutomationRule",
    "devices", "usage", "secrets", "user_permissions",
]
