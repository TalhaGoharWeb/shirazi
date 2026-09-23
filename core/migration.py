"""Mark-LIV -> SHIRAZI migration layer (Phase 3).

On startup this module detects a configuration written by the Mark-LIV era,
backs it up to ``config/backup/``, migrates compatible values to Shirazi
keys, and reports what happened — without ever destroying user data.

Guarantees:
  • The backup is written BEFORE anything is modified. A failed migration
    leaves the original file untouched.
  • Authentication, credentials, sessions and API integrations are preserved
    verbatim (``gemini_api_key``, per-plugin tokens under ``plugin_config``,
    device names, voices, flags). Only branding defaults and the brand
    marker are touched.
  • Old configs remain readable indefinitely: the on-disk format is unchanged
    JSON with the same keys, so a config from any era loads fine. The
    ``brand`` marker just records which era wrote it.
  • Idempotent: running twice is a no-op the second time.
  • Never raises — startup must not die because migration had a bad day.
    Every failure is captured in the returned report dict.

Dashboard token keys (``jarvis_token`` / ``jarvis_key`` /
``jarvis_device_token``) live in the *browser*, not in this config file.
They are handled by a permanent compat read path: the phone UI reads the
new ``shirazi_*`` keys first and falls back to the legacy ``jarvis_*``
keys, so a phone paired under Mark-LIV keeps working after the upgrade.
See docs/LEGACY_COMPAT.md.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from memory.config_manager import CONFIG_DIR, CONFIG_FILE, ensure_config_dir

BACKUP_DIR = CONFIG_DIR / "backup"

BRAND = "shirazi"
BRAND_VERSION = "3"          # phase that introduced the Shirazi brand
LEGACY_BRAND = "mark-liv"

# assistant_name values that were SHIP-DEFAULTS under Mark-LIV. If the user
# never changed the name, we rebrand the default; a custom name is sacred.
LEGACY_DEFAULT_NAMES = {"JARVIS", "J.A.R.V.I.S"}


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def detect_legacy_config() -> dict | None:
    """Return info about a pre-Shirazi config, or None if nothing to migrate.

    A config needs migration when the file exists but carries no Shirazi
    brand marker — i.e. it was written by Mark-LIV (or is simply ancient).
    """
    if not CONFIG_FILE.exists():
        return None
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"reason": "unreadable", "path": str(CONFIG_FILE)}
    if not isinstance(data, dict):
        return {"reason": "not-a-dict", "path": str(CONFIG_FILE)}
    if data.get("brand") == BRAND:
        return None  # already Shirazi — nothing to do
    return {
        "reason": "legacy-brand",
        "path": str(CONFIG_FILE),
        "brand": data.get("brand", LEGACY_BRAND),
        "keys": sorted(data.keys()),
        "assistant_name": data.get("assistant_name"),
    }


def backup_config() -> Path | None:
    """Copy the current config into config/backup/. Returns the backup path,
    or None if there was nothing to back up."""
    if not CONFIG_FILE.exists():
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    dest = BACKUP_DIR / f"api_keys.{_utc_stamp()}.json"
    shutil.copy2(CONFIG_FILE, dest)
    return dest


def migrate_config(logger: Callable[[str], None] = print) -> dict:
    """Back up, migrate, validate. Returns a status report dict.

    Report shapes:
      {"status": "fresh"}                          — no config file yet
      {"status": "current"}                        — already Shirazi
      {"status": "migrated", "backup": ..., ...}   — migrated just now
      {"status": "failed", "error": ...}           — backup ok, migrate failed
    """
    info = detect_legacy_config()
    if info is None:
        if not CONFIG_FILE.exists():
            return {"status": "fresh",
                    "message": "No existing config — fresh Shirazi install."}
        return {"status": "current",
                "message": "Config already carries the Shirazi brand marker."}

    if info.get("reason") in ("unreadable", "not-a-dict"):
        logger(f"[migrate] Config at {info['path']} is {info['reason']} — "
               f"leaving it alone, Shirazi will start with defaults.")
        return {"status": "failed", "error": info["reason"], "path": info["path"]}

    # 1. Back up BEFORE touching anything.
    try:
        backup = backup_config()
    except Exception as e:
        return {"status": "failed", "error": f"backup failed: {e}",
                "path": str(CONFIG_FILE)}
    logger(f"[migrate] Backed up Mark-LIV config -> {backup}")

    # 2. Migrate: preserve every user value, touch only branding.
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        preserved_keys = sorted(data.keys())

        name = (data.get("assistant_name") or "").strip()
        if not name or name in LEGACY_DEFAULT_NAMES:
            data["assistant_name"] = "SHIRAZI"
            renamed_default = True
        else:
            renamed_default = False  # custom name — sacred, keep verbatim

        data["brand"] = BRAND
        data["brand_version"] = BRAND_VERSION
        data["migrated_from"] = info.get("brand", LEGACY_BRAND)
        data["migrated_at"] = datetime.now(timezone.utc).isoformat()

        CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")

        # 3. Validate the round-trip.
        check = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        assert check.get("brand") == BRAND
        for k in preserved_keys:
            assert k in check, f"key lost in migration: {k}"
    except Exception as e:
        logger(f"[migrate] Migration failed ({e}) — original preserved at {backup}.")
        return {"status": "failed", "error": str(e)[:200],
                "backup": str(backup)}

    report = {
        "status": "migrated",
        "backup": str(backup),
        "preserved_keys": preserved_keys,
        "assistant_default_rebranded": renamed_default,
        "credentials_preserved": "gemini_api_key" in preserved_keys,
        "message": (
            "Migrated Mark-LIV config to Shirazi "
            f"(backup: {backup.name}). "
            f"{len(preserved_keys)} settings preserved; "
            + ("default assistant name rebranded to SHIRAZI."
               if renamed_default else "custom assistant name kept.")
        ),
    }
    logger(f"[migrate] {report['message']}")
    return report


def ensure_migrated(logger: Callable[[str], None] = print) -> dict:
    """Startup entry point. Detects, backs up, migrates, reports. Never raises."""
    ensure_config_dir()
    try:
        return migrate_config(logger=logger)
    except Exception as e:  # absolute last resort — boot must continue
        try:
            logger(f"[migrate] Unexpected migration error: {e}")
        except Exception:
            pass
        return {"status": "failed", "error": str(e)[:200]}
