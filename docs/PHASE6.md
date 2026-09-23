# Phase 6 — Agent Engine

**2026-09-23.** The agent engine: the full pipeline User → Intent Detection
→ Planner → Tool Selection → Permission Check → Tool Execution →
Observation → Reasoning/Next Action → Result, in one package:
`core/agent/`. It builds on the Phase-4 `ToolRegistry` + permission engine
and never bypasses them.

## What was built

| Area | Module | Status |
|---|---|---|
| Intent detection | `core/agent/intent.py` | **Working** — rule-based, deterministic, zero-network; 20 intents incl. COMPOUND ("Open Chrome and search for today's weather") |
| Planner | `core/agent/planner.py` | **Working** — deterministic multi-step plans, every step carries `why`; `plan.explain()` is user-readable |
| Agent loop | `core/agent/executor.py` | **Working** — async `run()`, worker-thread `run_sync()` / `run_in_background()`; permission gate per step; confirm-hook or PENDING_CONFIRMATION; provider reasoning for final answers |
| Browser tools | `core/agent/tools_browser.py` | **Working** — open/navigate/click/type/extract/search/download; Playwright optional, honest degradation |
| File tools | `core/agent/tools_files.py` | **Working** — search/read/create/rename/move/copy/delete/open/summarize; reuses `actions/file_controller.py` (+ `file_processor.py` for summarize) |
| System/media tools | `core/agent/tools_system.py` | **Working** — reuses `core/telemetry.py`, `actions/system_monitor.py`, `actions/computer_settings.py` (volume/media) |
| Computer tools | `core/agent/tools_computer.py` | **Working** — mouse/keyboard/screenshot/windows/apps/clipboard/notify; reuses `computer_control.py`, `open_app.py`; mouse-move SAFE, the rest gated per spec |
| Shell | `core/agent/shell.py` | **Working** — `shell_run` is the only shell path and routes exclusively through `request_shell_execution()` (human gate) |
| Research mode | `core/agent/research.py` | **Working** — keyless ddgs search + page fetch; claims labelled FACT / SOURCE / INFERENCE / UNCERTAINTY; citations only from real retrievals |
| Vision | `core/agent/vision.py` | **Working** — screen capture (mss), OCR via tesseract when installed, honest "unavailable" when not; downscaled images only |
| Registration | `core/agent/register.py` | **Working** — 46 ToolSpecs into the Phase-4 registry + Live declarations for `main.py` |

## Wiring points in existing code (minimal diffs)

- `main.py`: `ShiraziLive.__init__` builds `self.agent` (guarded — a broken
  agent package never breaks boot) and installs the 46 tool specs into the
  central registry; `_execute_tool` gained one branch dispatching agent
  tools via `Agent.execute_tool` (permission gate runs first, as for every
  tool); `_build_config` appends `agent_declarations()` so the Live model
  can call the new tools through the normal path.
- `core/permissions.py`: `_DEFAULT_LEVELS` extended with all 46 agent tool
  names (reads SAFE/READ_ONLY, mutations USER_CONFIRMATION, fail-closed
  default kept for unknown tools).
- `core/events.py`: `agent.plan` / `agent.step` / `agent.done` added to the
  topic contract.

## Genuinely working vs. limitation (honest)

**Working:** intent routing, explainable planning, per-step permission
gating (proven: delete/shell park without executing), the no-silent-shell
rule (proven: marker-file test + static check), research claim labelling
with real citations, the full async loop on workers, event emission,
registry install, Live wiring.

**Honest limitations (documented, not faked):**
- **Playwright is not installed** (sandbox and, per requirements, optional).
  `browser_open`/`browser_extract` fall back to *labelled* static fetches;
  `browser_navigate`/`browser_click`/`browser_type` report unavailability
  with install instructions. Nothing claims browser automation works without
  the browser engine.
- **tesseract is not installed.** OCR returns "unavailable" + install
  instructions; screen *capture* works, *understanding* is OCR text blocks.
- **No display / psutil / pyautogui in the sandbox.** Screenshot, mouse,
  keyboard, volume and process tools return their honest backend messages.
  All Windows paths are manual QA (`docs/WINDOWS_QA.md` §8).
- The provider chain had no keys in the sandbox, so final-answer reasoning
  falls back to labelled raw observations — the loop never invents an
  answer when the provider is unreachable.

## Verification

- `python -m unittest discover tests` — **166 tests, OK** (38 new Phase-6
  tests: intent routing, planner explainability, permission gating of
  delete/shell/browser-click/keyboard-hotkey/mouse-click/app-open,
  no-silent-shell proof, research FACT/SOURCE/UNCERTAINTY labelling,
  no-fabricated-citations, zero-source honesty, loop with mocked provider,
  background future, real file round-trip through the executor,
  Playwright/tesseract honesty, registry levels, redaction).
- Every new/changed Python file passes `py_compile` (incl. CRLF `main.py`).

## Cost honesty

Everything in the agent engine is free: rule-based intent/planning (no
model calls), keyless ddgs search, local tesseract/mss, the existing
provider chain for reasoning (free tiers per Phase 4). No new paid
dependency, no new network service, no new secret.

## Deferred (with reasons)

- **LLM-assisted planning** (dynamic replan mid-loop): the loop replans only
  via the final reasoning step today. Full LLM next-action planning needs a
  reliable JSON tool-call contract per provider — Phase 7+ candidate.
- **Typed-text → agent route**: `_on_text_command` still goes to the Live
  session (conversational path). Routing typed commands through
  `Agent.run_sync` is a small Phase-7 change with a big UX payoff.
- **Urdu/Roman-Urdu intent patterns**: the rule lexicon is English. The
  Live model handles Urdu conversationally; structured Urdu commands are a
  Phase-7 i18n task.
- **Dashboard exposure** of the agent loop (`/api/agent`) — natural Phase-7
  work alongside the mobile dashboard.
