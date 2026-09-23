# Phase 4 — Architecture Upgrade

**2026-09-23.** The architecture layer: AI provider abstraction, central tool
registry, generalized permission engine, layered memory, event bus, device
manager, configurable system prompt, i18n skeleton, structured logging.

## What was built

| Area | Module | Status |
|---|---|---|
| Provider abstraction | `core/providers/` (`base`, `registry`, `gemini`, `gemini_live`, `openrouter`, `ollama`, `future`) | **Working** (mocked-transport tests pass; live calls need keys/network) |
| Provider chain config | `config/providers.json` | **Wired** — chain `gemini → openrouter → ollama`; 3.x ladders, 2.5 deprecated |
| Tool registry | `core/tools/` (`registry.py`) | **Working** — adapter over inline/actions/plugins; `build_registry()` called in `ShiraziLive.__init__` |
| Permission engine | `core/permissions.py` | **Working & wired** — every `_execute_tool` call passes `check()`; USER_CONFIRMATION tools park behind the on-screen banner |
| Shell gate | `core/permissions.request_shell_execution()` + `actions/dev_agent._run_project` | **Working** — LLM-generated commands cannot reach the OS without the human gate |
| Layered memory | `core/memory/` (`store.py`, `adapters.py`) | **Working** — SQLite, 4 layers, inspect/delete; legacy JSON mirrored read-only |
| Event bus | `core/events.py` | **Working** — pub/sub, thread-safe, typed topic contract |
| Device manager | `core/devices.py` | **Working** — background enumeration, name-based selection, `config/devices.json` wired |
| System prompt | `core/prompt.py` | **Working** — builder done; `main.py` adoption scheduled Phase 5 |
| i18n | `i18n/` (loader + en/ur/ar/ur-Latn + RTL + extraction plan) | **Skeleton working** — UI string extraction is Phase 5 |
| Observability | `core/logger.py` | **Working** — fixed tags, secret redaction |

## Wiring points in existing code (minimal diffs)

- `main.py`: `LIVE_MODEL` now reads `config/providers.json` (`gemini_live.live_model`
  → `gemini.live_model` → shipped default); `ShiraziLive.__init__` builds
  `self._tool_registry`; `run()` configures permissions (`developer_mode` /
  `safe_mode` from `api_keys.json`), starts the `DeviceManager` (background
  enumeration), and sets the i18n language; `_execute_tool` runs the
  permission gate first and `_execute_tool_gated` parks confirmed tools
  behind `core/confirm.py`.
- `core/gemini.py`: ladders are config-driven (`fast_models` / `smart_models` /
  `search_models` in `providers.json`); defaults are the sane 3.x ladder with
  2.5 pins as deprecated fallbacks; new `call_once()` (single model, raises
  instead of swallowing) and `default_text_ladder()` / `cool_model()` /
  `reload_ladders()` for the provider layer.
- `actions/dev_agent.py`: `_run_project` is now a thin shell-gate wrapper;
  the real work moved to `_run_project_impl`. `pip install` of planned
  dependencies stays under the dev_agent-level confirmation banner (the
  banner shows the tool args, which include the dependency list).
- `config/.gitignore`: `shirazi_memory.db`, `*.db` added.

## Genuinely working vs. interface-only

**Working:** provider chain failover (unit-tested with mocked transports),
permission checks incl. the fail-closed default for unknown tools, the
confirm-banner flow for gated tools, dev_agent shell gating, SQLite memory
with inspect/delete, event bus, device selection persistence, prompt builder,
i18n loader + RTL helpers, logger redaction.

**Interface-only (documented, not faked):**
- `GeminiLiveProvider.create_session()` raises `NotImplementedError` with the
  contract — the voice session lifecycle is (correctly) in main.py's
  `ShiraziLive`. A standalone session manager is a future phase.
- `core/prompt.py` is built and tested but `main.py._build_config()` still
  assembles the prompt its own way; adoption is Phase 5 (the prompt text and
  tokens are unchanged, so this is a safe cutover).
- `web_search`/`browser_control` etc. keep their own implementations; the
  tool registry only *describes* them.

## Deferred (with reasons)

- **Encrypted secret storage** (`api_keys.json` still plaintext): scheduled
  per `docs/SECURITY_NOTES.md`; Phase 4 did not proliferate new secrets.
- **main.py adopting `core/prompt.build()`**: Phase 5, alongside the UI
  string extraction (the prompt is user-visible).
- **Urdu/Arabic lip-sync** (`core/viseme.py` audio-only fallback): needs a
  pronunciation layer; Phase 5/6.
- **Hey-Shirazi wake model**: no trained model exists; the legacy
  `hey_jarvis` model stays per `docs/LEGACY_COMPAT.md`.
- **Dashboard auth overhaul** (PIN entropy, salt, TLS default): Phase 7/8
  per `docs/SECURITY_NOTES.md`.
- **Windows runtime verification**: the sandbox is Linux; all
  `win32`-guarded paths remain UNVERIFIED on real Windows.

## Cost honesty

Tier labels are enforced in code and config: `Local (Free)` (Ollama — needs
the model downloaded, no quota), `Free API Tier` (Gemini, OpenRouter `:free`
— keyed, quota- and rate-limited), `Optional Paid Provider` (none configured;
the `future.py` hook documents it). `OpenRouterProvider` with `free_only=true`
refuses non-`:free` model ids rather than billing. Nothing advertises
unlimited free AI.

## Urgent: Gemini 2.5 REST retirement (2026-10-16)

`core/gemini.py` no longer leads with 2.5 pins. The shipped ladders are
3.x-first (`gemini-3.6-flash`), with 2.5 names kept as deprecated fallbacks
that log a warning when actually used. Model names are configurable in
`config/providers.json` without code changes (`fast_models`, `smart_models`,
`search_models`, `live_model`).
