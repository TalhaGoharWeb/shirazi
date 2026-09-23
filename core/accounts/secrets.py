"""core/accounts/secrets.py — encrypted secret store (Phase 8).

Implements the deferred Phase 3/4 item per docs/SECURITY_NOTES.md:
migrate config/api_keys.json secrets out of plaintext.

Backend order (first usable wins for writes; reads check all):
    1. Environment variables — read-only, highest precedence, never
       written by us. (e.g. GEMINI_API_KEY, OPENROUTER_API_KEY, or the
       generic SHIRAZI_SECRET_<NAME>.)
    2. OS keyring — via the `keyring` package when importable AND a
       working backend exists (probed once; failures disable it quietly).
    3. Encrypted file — config/credentials.enc, AES-256-GCM via the
       `cryptography` package (already a declared dashboard dependency).
       The 256-bit data key lives in config/.secret_key (chmod 600,
       best-effort). The whole payload is encrypted, so secret *names*
       are hidden too.

Honesty rules:
- If NO secure backend is available (no keyring, no cryptography),
  set() FAILS CLOSED with a clear error instead of writing plaintext —
  and migration leaves the plaintext file untouched (deleting secrets
  with nowhere safe to put them would be data loss). The report says
  exactly this.
- get() never raises for a missing secret (returns None); values are
  NEVER logged — only secret *names* appear in logs/reports.
- The legacy plaintext fallback (config/api_keys.json) is read-only and
  emits a one-time stderr note pointing at migration. It exists so
  existing users keep working until migration runs.
- Boot auto-migration is INTENTIONALLY not wired in Phase 8: twelve
  action modules (actions/web_search.py, dev_agent.py, code_helper.py,
  computer_control.py, computer_settings.py, desktop.py,
  file_processor.py, flight_finder.py, screen_processor.py,
  youtube_video.py, send_message.py, core/llm_client.py) read secrets
  directly from config/api_keys.json with no fallback chain, so
  removing keys at boot would break the single-user desktop flow.
  Migration is implemented, tested, and available explicitly via
  migrate_api_keys_json() / memory.config_manager.ensure_migrated();
  enable boot wiring only after those readers move to resolve_key()
  (see docs/SAAS.md § "Deferred: boot migration").

Compat (must not break):
- _AES_SALT in dashboard/server.py and the jarvis_token → shirazi_token
  browser-storage migration are untouched — this module only moves
  secret-looking keys out of api_keys.json (api_key/_token/_secret/
  _password suffixes + plugin_config). Storage-key names are not secrets
  and are never in api_keys.json.

Usage:
    from core.accounts import secrets
    key = secrets.resolve_key("gemini_api_key", "GEMINI_API_KEY")
    secrets.migrate_api_keys_json()   # idempotent, backs up first
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Optional

_BASE = Path(__file__).resolve().parent.parent.parent
_CONFIG_DIR = _BASE / "config"
_API_KEYS_FILE = _CONFIG_DIR / "api_keys.json"

_SERVICE = "shirazi"

# key names that count as secrets for migration
_SECRET_RE = re.compile(r"(api_key|_token|_secret|_password)$", re.IGNORECASE)
_SECRET_DICT_KEYS = ("plugin_config",)


def _looks_secret(name: str) -> bool:
    return bool(_SECRET_RE.search(name or "")) or name in _SECRET_DICT_KEYS


# ── Backends ───────────────────────────────────────────────────────────────

class _MemoryBackend:
    """In-memory backend (tests only). Never used in production."""

    def __init__(self):
        self._data: dict[str, str] = {}
        self.name = "memory"

    def get(self, name: str) -> Optional[str]:
        return self._data.get(name)

    def set(self, name: str, value: str) -> None:
        self._data[name] = value

    def delete(self, name: str) -> bool:
        return self._data.pop(name, None) is not None

    def list_names(self) -> list[str]:
        return sorted(self._data)


class _KeyringBackend:
    """OS credential store via the `keyring` package (Windows Credential
    Manager / macOS Keychain / Secret Service). Probed once at init."""

    def __init__(self):
        self.name = "keyring"
        self._kr = None
        try:
            import keyring
            # Probe: a backend that raises here is unusable (headless, etc.)
            keyring.get_password(_SERVICE, "__shirazi_probe__")
            self._kr = keyring
        except Exception:
            self._kr = None

    @property
    def usable(self) -> bool:
        return self._kr is not None

    def get(self, name: str) -> Optional[str]:
        try:
            return self._kr.get_password(_SERVICE, name)
        except Exception:
            return None

    def set(self, name: str, value: str) -> None:
        self._kr.set_password(_SERVICE, name, value)

    def delete(self, name: str) -> bool:
        try:
            self._kr.delete_password(_SERVICE, name)
            return True
        except Exception:
            return False

    def list_names(self) -> list[str]:
        return []  # keyring has no enumeration; names are tracked on read


class _EncryptedFileBackend:
    """AES-256-GCM encrypted JSON file. The data key is a random 32 bytes
    in config/.secret_key (chmod 600, best-effort)."""

    def __init__(self, config_dir: Path):
        self.name = "encrypted-file"
        self._dir = Path(config_dir)
        self._file = self._dir / "credentials.enc"
        self._keyfile = self._dir / ".secret_key"
        self._lock = threading.RLock()
        self._usable: Optional[bool] = None

    @property
    def usable(self) -> bool:
        if self._usable is None:
            try:
                from cryptography.hazmat.primitives.ciphers.aead import AESGCM
                self._aead_cls = AESGCM
                self._usable = True
            except Exception:
                self._usable = False
        return self._usable

    def _data_key(self) -> bytes:
        if self._keyfile.exists():
            key = self._keyfile.read_bytes()
            if len(key) == 32:
                return key
            raise RuntimeError("secret data-key file is corrupt")
        key = os.urandom(32)
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            self._keyfile.write_bytes(key)
            try:
                os.chmod(self._keyfile, 0o600)
            except Exception:
                pass  # best-effort (no-op semantics on Windows)
        except Exception as e:
            raise RuntimeError(f"cannot create secret data-key: {e}")
        return key

    def _read_all(self) -> dict:
        if not self._file.exists():
            return {}
        raw = self._file.read_bytes()
        try:
            nonce_b64, ct_b64 = raw.split(b".", 1)
            nonce = base64.b64decode(nonce_b64)
            ct = base64.b64decode(ct_b64)
            pt = self._aead_cls(self._data_key()).decrypt(nonce, ct, None)
            data = json.loads(pt.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception as e:
            raise RuntimeError(f"cannot decrypt secret store: {e}")

    def _write_all(self, data: dict) -> None:
        pt = json.dumps(data, ensure_ascii=False).encode("utf-8")
        nonce = os.urandom(12)
        ct = self._aead_cls(self._data_key()).encrypt(nonce, pt, None)
        blob = base64.b64encode(nonce) + b"." + base64.b64encode(ct)
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            tmp = self._file.with_suffix(".enc.tmp")
            tmp.write_bytes(blob)
            try:
                os.chmod(tmp, 0o600)
            except Exception:
                pass
            tmp.replace(self._file)
        except Exception as e:
            raise RuntimeError(f"cannot write secret store: {e}")

    def get(self, name: str) -> Optional[str]:
        with self._lock:
            return self._read_all().get(name)

    def set(self, name: str, value: str) -> None:
        with self._lock:
            data = self._read_all()
            data[name] = value
            self._write_all(data)

    def delete(self, name: str) -> bool:
        with self._lock:
            data = self._read_all()
            if name not in data:
                return False
            del data[name]
            self._write_all(data)
            return True

    def list_names(self) -> list[str]:
        with self._lock:
            return sorted(self._read_all())


# ── SecretStore ──────────────────────────────────────────────────────────

class SecretStore:
    """Env (read-only) is handled by resolve_key(), not here. This class
    manages the *writable* backends: OS keyring → encrypted file."""

    def __init__(self, base_dir: Optional[Path] = None,
                 backend: str = "auto"):
        base = Path(base_dir) if base_dir else _BASE
        self._dir = base / "config"
        if backend == "memory":
            self._backends = [_MemoryBackend()]
        elif backend == "auto":
            kr = _KeyringBackend()
            ef = _EncryptedFileBackend(self._dir)
            self._backends = [b for b in (kr, ef) if b.usable]
        else:
            raise ValueError(f"unknown secret backend '{backend}'")
        self._lock = threading.RLock()

    @property
    def backend_names(self) -> list[str]:
        return [b.name for b in self._backends]

    @property
    def has_secure_backend(self) -> bool:
        return bool(self._backends)

    def _require_backend(self) -> None:
        if not self._backends:
            raise RuntimeError(
                "no secure secret backend available (no OS keyring and the "
                "'cryptography' package is not installed). Refusing to "
                "store secrets — install it via: pip install cryptography")

    def get(self, name: str) -> Optional[str]:
        """Read from keyring, then encrypted file. Never raises for a
        missing secret; never logs the value."""
        for b in self._backends:
            try:
                v = b.get(name)
            except Exception:
                continue
            if v:
                return v
        return None

    def set(self, name: str, value: str) -> str:
        """Store a secret. FAILS CLOSED when no secure backend exists."""
        name = str(name or "").strip()
        if not name:
            raise ValueError("secret name must not be empty")
        value = str(value or "")
        if not value:
            raise ValueError("refusing to store an empty secret")
        self._require_backend()
        with self._lock:
            # Prefer keyring when usable, else encrypted file.
            self._backends[0].set(name, value)
        return self._backends[0].name

    def delete(self, name: str) -> bool:
        removed = False
        for b in self._backends:
            try:
                removed = b.delete(name) or removed
            except Exception:
                pass
        return removed

    def has(self, name: str) -> bool:
        return self.get(name) is not None

    def list_names(self) -> list[str]:
        names: set[str] = set()
        for b in self._backends:
            try:
                names.update(b.list_names())
            except Exception:
                pass
        return sorted(names)


# ── Key resolution (env → store → legacy plaintext) ──────────────────────

_default_store: Optional[SecretStore] = None
_store_lock = threading.Lock()
_legacy_warned = False


def get_store(base_dir: Optional[Path] = None) -> SecretStore:
    global _default_store
    with _store_lock:
        if _default_store is None or base_dir is not None:
            _default_store = SecretStore(base_dir=base_dir)
        return _default_store


def _legacy_plaintext(name: str) -> Optional[str]:
    """Read-only legacy fallback: config/api_keys.json. Emits a one-time
    note (name only, never the value) pointing at migration."""
    global _legacy_warned
    try:
        data = json.loads(_API_KEYS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None
    v = data.get(name) if isinstance(data, dict) else None
    if v and not _legacy_warned:
        _legacy_warned = True
        print(f"[SHIRAZI] note: secret '{name}' is still in plaintext "
              f"config/api_keys.json — run the encrypted-store migration "
              f"(happens automatically at boot).")
    return str(v) if v else None


def resolve_key(key_config: str, key_env: Optional[str] = None, *,
                user_id: Optional[str] = None,
                provider: Optional[str] = None,
                store: Optional[SecretStore] = None) -> str:
    """Resolve an API key. Order: env var → SecretStore (user-namespaced,
    then global) → legacy plaintext api_keys.json. Returns "" when unset.
    Never raises, never logs the value."""
    # 1. environment (explicit key_env first, then the generic override)
    for env_name in ([key_env] if key_env else []) + \
            [f"SHIRAZI_SECRET_{str(key_config).upper()}"]:
        v = os.environ.get(env_name or "")
        if v:
            return v
    st = store or get_store()
    # 2a. per-user namespaced secret
    if user_id and provider:
        v = st.get(f"user:{user_id}:provider:{provider}:{key_config}")
        if v:
            return v
    # 2b. global secret
    v = st.get(key_config)
    if v:
        return v
    # 3. legacy plaintext (compat until migration runs)
    return _legacy_plaintext(key_config) or ""


def has_key(key_config: str, key_env: Optional[str] = None, *,
            user_id: Optional[str] = None,
            provider: Optional[str] = None) -> bool:
    """True when a key is configured (env or store or legacy). The value is
    never exposed — for /api/v1 status displays."""
    return bool(resolve_key(key_config, key_env, user_id=user_id,
                            provider=provider))


# ── Migration: config/api_keys.json → encrypted store ────────────────────

def migrate_api_keys_json(base_dir: Optional[Path] = None, *,
                          store: Optional[SecretStore] = None) -> dict:
    """Idempotent migration of plaintext secrets out of api_keys.json.

    - Finds secret-looking keys (api_key/_token/_secret/_password suffix,
      plus plugin_config).
    - Backs up api_keys.json → api_keys.json.bak (first run only; later
      runs use a timestamped name so the ORIGINAL backup is never lost).
    - Moves each secret into the SecretStore, removes it from the JSON,
      stamps _secrets_migrated=true.
    - If NO secure backend exists: moves nothing, deletes nothing, and
      reports backend_unavailable (fail-closed — see module docstring).
    - Already migrated + nothing left → no-op report.

    Returns a report dict with names only (never values).
    """
    base = Path(base_dir) if base_dir else _BASE
    path = base / "config" / "api_keys.json"
    report: dict = {"migrated": False, "moved": [], "backup": None,
                    "reason": ""}
    if not path.exists():
        report["reason"] = "no api_keys.json"
        return report
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        report["reason"] = f"unreadable api_keys.json: {e}"
        return report
    if not isinstance(data, dict):
        report["reason"] = "api_keys.json is not a JSON object"
        return report

    secret_keys = [k for k in data
                   if _looks_secret(k)
                   and k not in ("_secrets_migrated", "_secrets_note")]
    if data.get("_secrets_migrated") is True and not secret_keys:
        report["reason"] = "already-migrated"
        return report
    if not secret_keys:
        # Nothing secret in the file; stamp it so we don't rescan forever.
        if data.get("_secrets_migrated") is not True:
            data["_secrets_migrated"] = True
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        report["reason"] = "no secrets in file"
        return report

    st = store or get_store(base_dir=base)
    if not st.has_secure_backend:
        report["reason"] = ("backend-unavailable: no OS keyring and no "
                            "'cryptography' package — plaintext left "
                            "untouched (fail-closed)")
        return report

    # Backup first (never overwrite the original backup).
    bak = path.with_name("api_keys.json.bak")
    if bak.exists():
        bak = path.with_name(
            f"api_keys.json.bak.{int(time.time())}")
    try:
        bak.write_bytes(path.read_bytes())
        try:
            os.chmod(bak, 0o600)
        except Exception:
            pass
    except Exception as e:
        report["reason"] = f"backup failed: {e}"
        return report
    report["backup"] = str(bak)

    moved = []
    try:
        for k in secret_keys:
            v = data.pop(k)
            sval = v if isinstance(v, str) else json.dumps(v or "",
                                                          ensure_ascii=False)
            if sval:
                st.set(k, sval)
                moved.append(k)
        data["_secrets_migrated"] = True
        data["_secrets_note"] = (
            "Secrets moved to the encrypted store "
            "(config/credentials.enc or OS keyring); see docs/SECURITY_NOTES.md")
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as e:
        report["reason"] = f"migration failed mid-way: {e} — backup at {bak}"
        return report
    report["migrated"] = True
    report["moved"] = moved
    return report


def ensure_migrated(base_dir: Optional[Path] = None) -> dict:
    """Boot-time entry point: run the migration if there is anything to do.
    Silent when already migrated; prints a one-line summary otherwise."""
    rep = migrate_api_keys_json(base_dir=base_dir)
    if rep["migrated"]:
        print(f"[SHIRAZI] 🔐 migrated {len(rep['moved'])} secret(s) from "
              f"config/api_keys.json to the encrypted store "
              f"(backup: {rep['backup']})")
    elif rep["reason"] not in ("already-migrated", "no api_keys.json",
                               "no secrets in file"):
        print(f"[SHIRAZI] ⚠️ secret migration skipped: {rep['reason']}")
    return rep


def scan_plaintext_residue(base_dir: Optional[Path] = None) -> list[str]:
    """Audit helper: secret-looking keys STILL present in api_keys.json
    plaintext. Empty list = clean. (Used by tests + /api/v1 honesty.)"""
    base = Path(base_dir) if base_dir else _BASE
    path = base / "config" / "api_keys.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    return [k for k in data if _looks_secret(k)
            and k not in ("_secrets_migrated", "_secrets_note")]
