"""core/prompt.py — the centralized, configurable system prompt (Phase 4).

Everything the model is told about *itself* is derived here from config —
never hard-coded in the template and never scattered across call sites.
main.py's ShiraziLive._build_config() assembles the per-session prompt today;
this module is the single builder it (and the dashboard, and tests) should
use — adoption in main.py is scheduled for Phase 5 (see docs/PHASE4.md).

Configured axes:
    assistant_name / user_name   — identity block
    language                     — en | ur | ar | ur-Latn (see i18n/)
    personality                  — a named preset below, or a custom dict
    tool permissions             — rendered from the tool registry: which
                                   tools exist and what level each needs
    automation permissions       — which autonomous behaviours are allowed
                                   (proactive briefs, background monitors…)
    research_mode                — when on: cite sources, say "I don't know"
    developer_mode               — unlocks PRIVILEGED tools (still confirmed)
    safe_mode                    — forces confirmation on READ_ONLY too,
                                   denies PRIVILEGED outright
    user customization           — config "prompt_overrides": appended
                                   verbatim as user instructions (highest
                                   precedence after safety)

The Phase-3 Shirazi persona ("shirazi") is the default.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

_BASE = Path(__file__).resolve().parent.parent
_TEMPLATE_PATH = _BASE / "core" / "prompt.txt"

LANGUAGES: dict[str, dict[str, Any]] = {
    "en":      {"name": "English",    "rtl": False},
    "ur":      {"name": "Urdu",       "rtl": True},
    "ar":      {"name": "Arabic",     "rtl": True},
    "ur-Latn": {"name": "Roman Urdu", "rtl": False},
}

# Named personalities. "shirazi" is the Phase-3 default — calm, professional,
# respectful, research-oriented. Custom dicts may supply any subset of keys.
PERSONALITIES: dict[str, dict[str, str]] = {
    "shirazi": {
        "tone": ("Intelligent and respectful. Calm and professional, never "
                 "theatrical. Concise when a short answer will do; detailed "
                 "when the user asks for depth or the task genuinely needs it."),
        "behaviour": ("Proactive: surface what matters without being asked, "
                       "but never nag. Technically capable — you operate this "
                       "machine, so act like someone who knows it."),
        "culture": ("Culturally respectful: meet the user in their language "
                    "and norms, especially across English, Urdu and Arabic."),
        "research": ("Research-oriented: check before you claim, cite what "
                      "you found, and say plainly when you do not know. "
                      "Helpful first, clever second."),
    },
    "concise": {
        "tone": "Terse and direct. Short answers first; expand only on request.",
        "behaviour": "Do the task, say what was done, stop.",
        "culture": "Match the user's language; keep formalities minimal.",
        "research": "Answer from tools, not memory; say when unsure.",
    },
    "formal": {
        "tone": "Formal and measured. Complete sentences, no slang.",
        "behaviour": "Confirm before acting on anything non-trivial.",
        "culture": "Use the respectful register of the user's language.",
        "research": "Cite sources for every factual claim.",
    },
    "friendly": {
        "tone": "Warm and personable, still professional.",
        "behaviour": "Proactive and encouraging; explain as you go.",
        "culture": "Meet the user in their language and norms.",
        "research": "Check before you claim; say plainly when you do not know.",
    },
}


def _load_template() -> str:
    try:
        return _TEMPLATE_PATH.read_text(encoding="utf-8")
    except Exception:
        return ("You are {assistant_name}, an AI assistant. Be intelligent, "
                "respectful, calm and professional. Never simulate or guess "
                "results — always call the appropriate tool.")


def _render(template: str, values: dict) -> str:
    """Plain token replace (not str.format): a stray brace in user wording
    must never take the app down at startup."""
    out = template or ""
    for key, val in values.items():
        out = out.replace("{" + key + "}", str(val))
    return out


def _personality(cfg: dict) -> dict[str, str]:
    name = (cfg.get("personality") or "shirazi")
    if isinstance(name, dict):
        custom = {k: str(v) for k, v in name.items()}
        merged = dict(PERSONALITIES["shirazi"])
        merged.update(custom)
        return merged
    return dict(PERSONALITIES.get(str(name), PERSONALITIES["shirazi"]))


def _language_block(cfg: dict) -> str:
    code = str(cfg.get("language") or "en")
    info = LANGUAGES.get(code, LANGUAGES["en"])
    lines = [f"[LANGUAGE]\nRespond in {info['name']} ({code})."]
    if code == "ur-Latn":
        lines.append("Write Urdu in Latin script (Roman Urdu), as used in "
                     "everyday Pakistani texting.")
    if info["rtl"]:
        lines.append("This language is right-to-left; keep UI-facing strings "
                     "RTL-safe.")
    return "\n".join(lines)


def _modes_block(cfg: dict) -> str:
    lines = ["[MODES]"]
    if cfg.get("research_mode"):
        lines.append("- RESEARCH MODE is ON: verify claims with the research "
                     "tools, cite sources inline, and say plainly when "
                     "something could not be verified.")
    else:
        lines.append("- Research mode is off: answer directly; use web search "
                     "only when the user asks or the answer needs freshness.")
    if cfg.get("developer_mode"):
        lines.append("- DEVELOPER MODE is on: PRIVILEGED tools are unlocked, "
                     "but they still require the user's on-screen confirmation "
                     "every time. Never treat developer mode as consent.")
    if cfg.get("safe_mode"):
        lines.append("- SAFE MODE is on: confirm with the user before any "
                     "action beyond trivial reads; PRIVILEGED tools are "
                     "denied outright.")
    return "\n".join(lines)


def _automation_block(cfg: dict) -> str:
    auto = cfg.get("automation") or {}
    if not isinstance(auto, dict):
        return ""
    allowed = [k for k, v in auto.items() if v]
    denied = [k for k, v in auto.items() if not v]
    lines = ["[AUTOMATION PERMISSIONS]"]
    if allowed:
        lines.append("Allowed without asking: " + ", ".join(allowed) + ".")
    if denied:
        lines.append("Never do unprompted: " + ", ".join(denied) + ".")
    if not allowed and not denied:
        return ""
    return "\n".join(lines)


def _permissions_block(tools=None, cfg: dict | None = None) -> str:
    from core.permissions import Level
    lines = ["[TOOL PERMISSIONS]",
             "You never run shell commands silently. If a task needs a command "
             "run, say what it is and wait: the user confirms it on screen "
             "before anything executes."]
    if tools is None:
        return "\n".join(lines)
    try:
        gated = tools.list_by_permission(Level.USER_CONFIRMATION)
        priv = tools.list_by_permission(Level.PRIVILEGED)
    except Exception:
        return "\n".join(lines)
    if gated:
        lines.append("These tools ask the user to confirm on screen before "
                     "they run: " + ", ".join(sorted(t.name for t in gated)) + ".")
    if priv:
        lines.append("These tools are PRIVILEGED (developer mode + confirmation): "
                     + ", ".join(sorted(t.name for t in priv)) + ".")
    lines.append("When a tool needs confirmation, say ONE short sentence in "
                 "the user's language asking them to confirm it on screen — "
                 "do not claim it is done.")
    return "\n".join(lines)


def build(config: Optional[dict] = None, *, tools=None,
          platform: str = "", capabilities: str = "", limits: str = "",
          memory_text: str = "", time_context: str = "",
          identity_context: str = "") -> str:
    """Build the full system prompt from config.

    `config` merges config/settings.json (shipped defaults) with
    config/api_keys.json (user prefs). `tools` is a core/tools ToolRegistry.
    Returns the complete prompt text, ready as the Live session's
    system_instruction.
    """
    cfg = dict(config or {})
    asst = str(cfg.get("assistant_name") or "SHIRAZI").strip()

    template = _load_template()
    rendered = _render(template, {
        "assistant_name": asst,
        "platform": platform,
        "capabilities": capabilities,
        "limits": limits,
    })

    persona = _personality(cfg)
    persona_block = ("[PERSONA]\n" + "\n".join(
        f"{v}" for v in persona.values() if v))

    parts = [
        time_context.strip(),
        identity_context.strip(),
        memory_text.strip(),
        rendered.strip(),
        persona_block,
        _language_block(cfg),
        _permissions_block(tools, cfg),
        _modes_block(cfg),
        _automation_block(cfg),
    ]
    overrides = cfg.get("prompt_overrides")
    if overrides:
        parts.append("[USER INSTRUCTIONS]\n" + str(overrides).strip())

    return "\n\n".join(p for p in parts if p)


def describe_config(config: Optional[dict] = None) -> dict:
    """The effective prompt configuration, for the settings UI / diagnostics."""
    cfg = dict(config or {})
    code = str(cfg.get("language") or "en")
    return {
        "assistant_name": str(cfg.get("assistant_name") or "SHIRAZI"),
        "language": code,
        "language_name": LANGUAGES.get(code, LANGUAGES["en"])["name"],
        "rtl": LANGUAGES.get(code, LANGUAGES["en"])["rtl"],
        "personality": cfg.get("personality") or "shirazi",
        "research_mode": bool(cfg.get("research_mode", False)),
        "developer_mode": bool(cfg.get("developer_mode", False)),
        "safe_mode": bool(cfg.get("safe_mode", False)),
        "automation": cfg.get("automation") or {},
        "has_custom_overrides": bool(cfg.get("prompt_overrides")),
    }
