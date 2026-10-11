"""
SHIRAZI plugin: telegram_remote.

Two-way Telegram control for SHIRAZI: poll your bot for commands and
execute them as SHIRAZI tools. SECURITY-HARDENED (per the MARK-LVIII
audit): only a paired chat ID is ever served, and the dangerous
/launch + /kill commands use a strict allowlist instead of raw shell.

Setup (one time):
  1. Create a bot with @BotFather, get the token.
  2. In SHIRAZI config set plugin_config.telegram_remote = {
       "bot_token": "<token>", "chat_id": "<your numeric chat id>" }
     (get your chat id by messaging the bot, then calling action=setup)
  3. action=poll checks for new commands (call it on a schedule or by voice).

Commands from Telegram: /say <text> · /launch <app> · /kill <exe> ·
/lock · /brief · /photo (webcam snapshot to you)
"""

from __future__ import annotations

import json as _json
import subprocess as _sp
import sys as _sys
import urllib.request as _urlreq
import urllib.parse as _urlparse

PLUGIN = {
    "name": "telegram_remote",
    "description": (
        "Two-way Telegram remote control for SHIRAZI (poll for commands "
        "from your paired Telegram chat). Trigger on 'check telegram', "
        "'telegram setup', 'poll telegram'. Needs bot_token + chat_id in "
        "plugin config. Dangerous commands (/launch, /kill) are allowlisted."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "'setup' (show pairing steps) | 'poll' (fetch+run new commands).",
            },
        },
        "required": [],
    },
}

_API = "https://api.telegram.org/bot%s/%s"

# Hardening: /launch and /kill ONLY accept these exact names. Everything
# else is refused — this is the fix for the command-injection surface
# found in the reference implementation's os.system(f"start {app}") code.
_LAUNCH_ALLOW = {"notepad", "calc", "mspaint", "chrome", "code", "vlc"}
_KILL_ALLOW = {"notepad.exe", "calc.exe", "mspaint.exe", "chrome.exe",
               "code.exe", "vlc.exe"}


def _cfg() -> dict:
    try:
        from memory.config_manager import get_plugin_config
        return get_plugin_config("telegram_remote")
    except Exception:
        return {}


def _api(token: str, method: str, params: dict = None):
    url = _API % (token, method)
    data = _urlparse.urlencode(params or {}).encode() if params else None
    req = _urlreq.Request(url, data=data,
                          headers={"User-Agent": "SHIRAZI-telegram-remote/1.0"})
    with _urlreq.urlopen(req, timeout=15) as r:
        return _json.loads(r.read().decode("utf-8"))


def _send(token: str, chat_id: str, text: str) -> None:
    try:
        _api(token, "sendMessage",
             {"chat_id": chat_id, "text": text[:4000]})
    except Exception:
        pass


def _handle(cmd: str, token: str, chat_id: str, player) -> str:
    parts = cmd.strip().split(None, 1)
    verb = parts[0].lower().lstrip("/")
    arg = parts[1] if len(parts) > 1 else ""

    if verb == "say":
        return f"SHIRAZI says: {arg[:500]}"
    if verb == "lock":
        try:
            if _sys.platform.startswith("win"):
                import ctypes
                ctypes.windll.user32.LockWorkStation()
                return "Workstation locked."
            return "Lock is Windows-only."
        except Exception as e:
            return f"Lock failed: {e}"
    if verb == "launch":
        name = arg.strip().split()[0].lower() if arg.strip() else ""
        if name not in _LAUNCH_ALLOW:
            return (f"Refused '{name}'. Allowed: "
                    f"{', '.join(sorted(_LAUNCH_ALLOW))}.")
        try:
            if _sys.platform.startswith("win"):
                _sp.Popen(["cmd", "/c", "start", "", name],
                           stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
            else:
                _sp.Popen([name], stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
            return f"{name} launched."
        except Exception as e:
            return f"Launch failed: {e}"
    if verb == "kill":
        exe = arg.strip().split()[0].lower() if arg.strip() else ""
        if exe not in _KILL_ALLOW:
            return (f"Refused '{exe}'. Allowed: "
                    f"{', '.join(sorted(_KILL_ALLOW))}.")
        try:
            r = _sp.run(["taskkill", "/f", "/im", exe],
                        capture_output=True, timeout=10)
            return f"{exe} terminated." if r.returncode == 0 else \
                f"{exe} not running."
        except Exception as e:
            return f"Kill failed: {e}"
    if verb == "brief":
        try:
            from plugins.daily_briefing import run as briefing
            return briefing({}, player, None)
        except Exception:
            return "Briefing unavailable."
    if verb == "photo":
        return ("Photo capture is disabled in this build — enable it in "
                "plugin config (photo_enabled=true) after reviewing the "
                "privacy implications.")
    return ("Unknown command. Try /say, /launch, /kill, /lock, /brief.")


def run(parameters: dict, player=None, session_memory=None) -> str:
    action = str(parameters.get("action") or "poll").strip().lower()
    cfg = _cfg()
    token = str(cfg.get("bot_token") or "").strip()
    chat_id = str(cfg.get("chat_id") or "").strip()

    if action == "setup":
        return (
            "Telegram remote setup, sir. 1) Message @BotFather on Telegram, "
            "send /newbot, copy the token. 2) Message your new bot anything. "
            "3) In SHIRAZI settings set plugin_config.telegram_remote to "
            "{\"bot_token\": \"<token>\"}. 4) Say 'poll telegram' once — I "
            "will detect your chat ID from your message and pair it. "
            "After pairing, only your chat is ever served."
        )

    if not token:
        return ("Sir, no bot token configured. Say 'telegram setup' for "
                "the pairing steps.")
    try:
        offset = int(cfg.get("offset") or 0)
    except Exception:
        offset = 0

    try:
        resp = _api(token, "getUpdates",
                    {"offset": offset, "timeout": 5})
    except Exception as e:
        return f"Sir, Telegram is unreachable: {e}"
    if not resp.get("ok"):
        return "Sir, Telegram rejected the bot token. Check the config."

    results = []
    new_offset = offset
    for upd in resp.get("result", []):
        uid = upd.get("update_id", 0)
        new_offset = max(new_offset, uid + 1)
        msg = upd.get("message") or {}
        from_id = str((msg.get("from") or {}).get("id", ""))
        text = str(msg.get("text") or "").strip()
        if not text:
            continue
        # Pairing: first message from anyone pairs ONLY if no chat_id yet.
        if not chat_id:
            chat_id = from_id
            try:
                from memory.config_manager import save_plugin_config
                save_plugin_config("telegram_remote",
                                   {"chat_id": chat_id})
            except Exception:
                pass
            _send(token, chat_id,
                  "SHIRAZI paired. This chat is now the only authorized remote.")
            results.append("paired new chat")
            continue
        # Auth gate: ignore every chat except the paired one.
        if from_id != chat_id:
            continue
        out = _handle(text, token, chat_id, player)
        _send(token, chat_id, out)
        results.append(f"{text[:30]} → done")

    try:
        from memory.config_manager import save_plugin_config
        save_plugin_config("telegram_remote", {"offset": new_offset})
    except Exception:
        pass

    text = (f"Telegram poll complete, sir: {len(results)} command(s) handled."
            if results else
            "Telegram poll complete, sir: no new commands.")
    if player:
        try:
            player.write_log(f"SHIRAZI: {text}")
        except Exception:
            pass
    return text
