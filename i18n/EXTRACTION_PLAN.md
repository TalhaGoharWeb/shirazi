# Phase-5 UI String Extraction Plan

**Phase 4 delivered:** the loader (`i18n/__init__.py`), four catalogs
(`en.json`, `ur.json`, `ar.json`, `ur-Latn.json`), RTL helpers (`is_rtl()`,
`rtl_mark()`), and the config plumbing (`language` key → `core/prompt.py` →
`describe_config()`). New code must use `t()` and never hard-code
user-facing strings.

**Phase 5 must extract the existing strings** — roughly:

| Surface | Where the strings live today | Notes |
|---|---|---|
| HUD / MainWindow | `ui.py` — labels, tooltips, drawer titles (~hundreds) | Biggest job; do it per-widget |
| Overlays | `SetupOverlay`, `CustomizeOverlay`, `PluginManagerOverlay`, `PluginSettingsOverlay`, `AudioDeviceOverlay`, `MemoryOverlay`, `RemoteKeyOverlay`, `ConfirmBanner` | `ConfirmBanner` first — it's the permission UI |
| Chat log / status | `LogWidget`, `HudCanvas` status text | Use `t()` with `{placeholders}` |
| Phone dashboard | `dashboard/static/app.html`, `login.html` | Needs a JS-side catalog mirror |
| Action result strings | `actions/*.py` user-facing returns | Only strings shown/spoken to the user |
| Memory overlay | `ui.py` MemoryOverlay + `core/memory` inspect/delete | Already aligned with Phase-4 keys |

**Rules for the extraction pass:**

1. Keys are stable identifiers (`confirm_button`, not the English text).
   Never rename a key once shipped — the catalogs are a contract.
2. Every catalog must carry every key. CI check: a test that loads all four
   catalogs and fails on a missing key.
3. Placeholders are `{name}`-style and identical across languages.
4. RTL: `is_rtl()` drives `Qt.RightToLeft` layout direction on the main
   window; `rtl_mark()` wraps mixed LTR/RTL strings in labels and log lines.
5. Log/diagnostic strings (developer-facing) stay in English — only
   user-facing strings are translated.
6. The `language` config key is the single source of truth; the settings
   drawer gets a language picker that calls `i18n.set_language()` and
   re-renders.
