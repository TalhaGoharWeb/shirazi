"""
SHIRAZI plugin: voice_macros.

User-defined voice macros: map a spoken phrase to an action.
Actions: say(text) · open_app(name) · scene(name) · briefing().

Manage with action=add|list|remove. Macros persist in the plugin config
namespace "voice_macros" so they survive restarts. The LLM should call
'action=list' when the user asks "what are my macros", and action=add
when the user says "when I say X, do Y".
"""

from __future__ import annotations

PLUGIN = {
    "name": "voice_macros",
    "description": (
        "Manage custom voice macros: map a spoken phrase to an action. "
        "Trigger on 'add macro', 'when I say <phrase> do <thing>', 'my "
        "macros', 'remove macro <phrase>'. Actions: say | open_app | "
        "scene | briefing."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "'add' | 'list' | 'remove' | 'run'.",
            },
            "phrase": {
                "type": "STRING",
                "description": "The spoken trigger phrase.",
            },
            "do": {
                "type": "STRING",
                "description": "For add: 'say' | 'open_app' | 'scene' | 'briefing'.",
            },
            "arg": {
                "type": "STRING",
                "description": "For add: the text/app/scene the macro runs.",
            },
        },
        "required": ["action"],
    },
}

_VALID_DO = ("say", "open_app", "scene", "briefing")


def _load() -> dict:
    try:
        from memory.config_manager import get_plugin_config
        macros = get_plugin_config("voice_macros").get("macros", {})
        return dict(macros) if isinstance(macros, dict) else {}
    except Exception:
        return {}


def _save(macros: dict) -> None:
    from memory.config_manager import save_plugin_config
    save_plugin_config("voice_macros", {"macros": macros})


def run(parameters: dict, player=None, session_memory=None) -> str:
    action = str(parameters.get("action") or "list").strip().lower()
    phrase = str(parameters.get("phrase") or "").strip().lower()[:80]

    if action == "list":
        macros = _load()
        if not macros:
            return ("Sir, no voice macros yet. Say 'add macro: when I say "
                    "good morning, brief me'.")
        lines = [f"'{k}' → {v.get('do')}: {v.get('arg', '')[:40]}"
                 for k, v in list(macros.items())[:15]]
        return "Your macros, sir: " + " | ".join(lines)

    if action == "add":
        do = str(parameters.get("do") or "").strip().lower()
        arg = str(parameters.get("arg") or "").strip()[:200]
        if not phrase or do not in _VALID_DO:
            return ("Sir, I need a phrase and an action "
                    f"({', '.join(_VALID_DO)}).")
        macros = _load()
        macros[phrase] = {"do": do, "arg": arg}
        try:
            _save(macros)
        except Exception as e:
            return f"Sir, saving the macro failed: {e}"
        return f"Macro saved, sir: when you say '{phrase}', I will {do} {arg}."

    if action == "remove":
        macros = _load()
        if phrase in macros:
            del macros[phrase]
            try:
                _save(macros)
            except Exception:
                pass
            return f"Macro '{phrase}' removed, sir."
        return f"Sir, no macro named '{phrase}' found."

    if action == "run":
        # Invoked when a macro phrase is detected; executes the stored action.
        macros = _load()
        m = macros.get(phrase)
        if not m:
            return f"Sir, no macro named '{phrase}' found."
        do, arg = m.get("do"), str(m.get("arg", ""))
        if do == "say":
            return arg or "Done, sir."
        if do == "briefing":
            try:
                from plugins.daily_briefing import run as briefing
                return briefing({}, player, session_memory)
            except Exception:
                return "Briefing unavailable."
        if do == "scene":
            try:
                from plugins.scenes import run as scene_run
                return scene_run({"scene": arg}, player, session_memory)
            except Exception:
                return "Scene unavailable."
        if do == "open_app":
            name = arg.split()[0] if arg.split() else ""
            if not name or any(c in name for c in "\\/&|;$ "):
                return "Sir, that app name is not allowed."
            try:
                import subprocess, sys
                if sys.platform.startswith("win"):
                    subprocess.Popen(["cmd", "/c", "start", "", name],
                                     stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
                else:
                    subprocess.Popen([name], stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
                return f"{name} launched, sir."
            except Exception as e:
                return f"Sir, launching {name} failed: {e}"
        return "Sir, that macro action is unknown."

    return "Sir, action must be add, list, remove or run."
