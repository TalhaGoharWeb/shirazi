"""
dashboard/server.py — SHIRAZI Local HTTP Dashboard

Plain HTTP on port 8000 (no SSL warnings, no firewall issues).
Security at the application layer: AES-256-CBC with session-key-derived key.
CryptoJS is auto-downloaded once and served locally — no CDN needed after that.

Install deps:  pip install fastapi "uvicorn[standard]" cryptography
"""

import asyncio
import base64
import hashlib
import re
import secrets
import socket
import string
import time
from pathlib import Path
import json
import threading
import urllib.parse

# pyautogui is a hard runtime dep of the desktop app, but the dashboard
# must stay importable without it (tests, standalone runs). It is used
# only to resync the touchpad cursor estimate — guarded everywhere.
try:
    import pyautogui as _pag
except Exception:
    _pag = None

_DEPS_OK = False
try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
    from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
    import uvicorn
    _DEPS_OK = True
except ImportError:
    pass

# python-multipart is required for file uploads — optional dependency
_UPLOAD_OK = False
try:
    from fastapi import UploadFile, File as FastAPIFile
    _UPLOAD_OK = True
except Exception:
    pass

BASE_DIR    = Path(__file__).resolve().parent.parent
STATIC_DIR  = Path(__file__).parent / "static"
PORT        = 8000
MAX_UPLOAD_MB = 500

# ── PIN rate limiting (Phase 2) ─────────────────────────────────────────────
# Bounded brute-force protection for the PIN entry endpoints (/login and
# /auto-login). Failures are counted per client IP; a successful login clears
# the counter. Full auth overhaul is scheduled for Phase 7/8.
PIN_MAX_ATTEMPTS   = 5      # failed attempts before lockout
PIN_LOCKOUT_SECS   = 300    # lockout duration in seconds (5 min)
PIN_ATTEMPT_WINDOW = 600    # failures older than this stop counting (10 min)

# ── Phase 7: mobile dashboard (Siri-like voice, remote control) ─────────────
# Security model for the command channel (/ws/cmd): every message maps to
# ONE allowlisted tool with validated, typed parameters. permissions.check()
# validates every call (unknown tools fail closed, PRIVILEGED is denied).
# USER_CONFIRMATION-level remote-control tools (click, key press, app open)
# are treated as human-authorized: the physical tap on a PIN-paired phone
# IS the explicit approval. Free-text /api/agent keeps the strict per-step
# Approve/Deny phone flow, because the agent may plan risky multi-step work.
CONFIRM_TIMEOUT_SECS = 90.0   # matches core/confirm.py TIMEOUT_SECONDS
AGENT_TEXT_MAX       = 2000   # max chars for /api/agent input
CMD_RATE_MAX         = 120    # non-move /ws/cmd + /api/command msgs per 60 s / token
CMD_RATE_WINDOW      = 60.0
PAD_MOVE_RATE_MAX    = 40     # touchpad_move msgs per second / token (rest dropped)

# D-pad allowlist: pyautogui key names the phone may send. No modifiers,
# no function keys beyond F11, no arbitrary strings — ever.
DPAD_KEYS = ("up", "down", "left", "right", "enter", "space",
             "esc", "tab", "f11")

# Volume actions the phone may request (maps to agent tools_system tools).
VOLUME_ACTIONS = ("up", "down", "mute", "unmute", "set")

# Quick commands: 8 defaults, user-extensible via config/dashboard.json.
# Each runs through the agent (natural language) so permission gating
# applies exactly as if the user had typed the prompt.
DEFAULT_QUICK_COMMANDS = [
    {"id": "youtube",       "label": "YouTube",       "icon": "\U0001F3AC",
     "prompt": "Open YouTube in the browser"},
    {"id": "chrome",        "label": "Chrome",        "icon": "\U0001F310",
     "prompt": "Open Google Chrome"},
    {"id": "explorer",      "label": "Files",         "icon": "\U0001F4C1",
     "prompt": "Open the file manager"},
    {"id": "datetime",      "label": "Date & Time",   "icon": "\U0001F550",
     "prompt": "What is the current date and time?"},
    {"id": "weather",       "label": "Weather",       "icon": "\U0001F324\U0000FE0F",
     "prompt": "What is the weather like right now?"},
    {"id": "screen",        "label": "Screen Summary","icon": "\U0001F5A5\U0000FE0F",
     "prompt": "Summarize what is currently on my screen"},
    {"id": "joke",          "label": "Joke",          "icon": "\U0001F604",
     "prompt": "Tell me a short joke"},
    {"id": "notifications", "label": "Notifications", "icon": "\U0001F514",
     "prompt": "Check my recent notifications and summarize them"},
]


def _make_uploads_dir() -> Path:
    """Return (and create) the cross-platform uploads folder."""
    for candidate in [
        Path.home() / "Downloads" / "SHIRAZI Uploads",
        Path.home() / "Documents" / "SHIRAZI Uploads",
        BASE_DIR / "uploads",
    ]:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except Exception:
            pass
    return BASE_DIR / "uploads"


UPLOADS_DIR = _make_uploads_dir()

def _get_gemini_key() -> str | None:
    try:
        import json as _json
        with open(BASE_DIR / "config" / "api_keys.json", "r", encoding="utf-8") as f:
            return _json.load(f).get("gemini_api_key")
    except Exception:
        return None

_KEY_CHARS = [c for c in (string.ascii_uppercase + string.digits)
              if c not in ('O', 'I', 'L', '0', '1')]

# ── AES-256-CBC ───────────────────────────────────────────────────────────────
# NOTE: the salt VALUE is intentionally the legacy Mark-LIV string. It is a
# crypto-functional constant — changing it would break key agreement with
# already-paired phones. See docs/LEGACY_COMPAT.md.
_AES_SALT = b'JARVIS-DASHBOARD-v1'


def _derive_key(session_key: str) -> bytes:
    """SHA-256(sessionKey‖salt) → 32-byte AES-256 key (microseconds, no PBKDF2 needed)."""
    return hashlib.sha256(session_key.encode('utf-8') + _AES_SALT).digest()


def _decrypt_cbc(aes_key: bytes, enc_b64: str) -> str:
    """Decrypt base64(IV[16] ‖ ciphertext) with AES-256-CBC + PKCS7."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives import padding as sym_pad
    raw      = base64.b64decode(enc_b64)
    iv, ct   = raw[:16], raw[16:]
    dec      = Cipher(algorithms.AES(aes_key), modes.CBC(iv)).decryptor()
    padded   = dec.update(ct) + dec.finalize()
    unpadder = sym_pad.PKCS7(128).unpadder()
    return (unpadder.update(padded) + unpadder.finalize()).decode('utf-8')


def _summarise_args(args: dict) -> str:
    """One-line, redacted summary of tool args for the phone confirm card.
    Values are truncated and never logged raw (no secrets in the UI)."""
    parts = []
    for k, v in (args or {}).items():
        s = str(v)
        if len(s) > 60:
            s = s[:60] + "\u2026"
        parts.append(f"{k}={s}")
    return ", ".join(parts) if parts else "(no arguments)"


# ── CryptoJS (auto-download once, served locally) ─────────────────────────────
_CRYPTOJS_CDN  = ("https://cdnjs.cloudflare.com/ajax/libs/"
                  "crypto-js/4.2.0/crypto-js.min.js")
_CRYPTOJS_FILE = STATIC_DIR / "crypto-js.min.js"


def _ensure_network_access(port: int) -> None:
    """Cross-platform, best-effort: open port in the OS firewall for LAN access.

    Runs in a background thread — never blocks uvicorn startup.

    Windows : writes a .bat file, runs it elevated via Windows ShellExecuteW
              (native UAC dialog, guaranteed to appear). One-time setup.
    macOS   : osascript admin dialog if the Application Firewall is on.
    Linux   : pkexec GUI → sudo -n → prints manual command as fallback.
    """
    import sys, subprocess, os, tempfile, threading

    # ── Windows ──────────────────────────────────────────────────────────────
    if sys.platform == "win32":
        import ctypes, time

        port_rule = f"SHIRAZI Dashboard Port {port}"
        prog_rule  = "SHIRAZI Dashboard Python"
        py_exe     = sys.executable

        def _netsh_rule_exists(name: str) -> bool:
            try:
                r = subprocess.run(
                    ["netsh", "advfirewall", "firewall", "show", "rule", f"name={name}"],
                    capture_output=True, text=True, timeout=5,
                )
                return r.returncode == 0 and "No rules match" not in r.stdout
            except Exception:
                return False

        def _network_is_public() -> bool:
            try:
                r = subprocess.run(
                    ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                     "(Get-NetConnectionProfile | "
                     "Where-Object {$_.NetworkCategory -eq 'Public'} | "
                     "Measure-Object).Count"],
                    capture_output=True, text=True, timeout=6,
                )
                return r.stdout.strip() not in ("", "0")
            except Exception:
                return False

        need_port    = not _netsh_rule_exists(port_rule)
        need_prog    = not _netsh_rule_exists(prog_rule)
        need_private = _network_is_public()

        if not need_port and not need_prog and not need_private:
            return  # already fully configured

        # Build a .bat file — netsh + powershell, runs fast when elevated
        bat_lines = ["@echo off"]
        if need_private:
            bat_lines.append(
                'powershell -NoProfile -NonInteractive -Command "'
                'Get-NetConnectionProfile | '
                "Where-Object {$_.NetworkCategory -eq 'Public'} | "
                'Set-NetConnectionProfile -NetworkCategory Private"'
            )
        if need_port:
            bat_lines.append(
                f'netsh advfirewall firewall add rule '
                f'name="{port_rule}" protocol=TCP dir=in '
                f'localport={port} action=allow'
            )
        if need_prog:
            bat_lines.append(
                f'netsh advfirewall firewall add rule '
                f'name="{prog_rule}" dir=in action=allow '
                f'program="{py_exe}" enable=yes'
            )

        bat_body = "\r\n".join(bat_lines) + "\r\n"
        fd, bat_path = tempfile.mkstemp(suffix=".bat", prefix="shirazi_fw_")
        try:
            os.write(fd, bat_body.encode("mbcs"))   # Windows cmd.exe expects ANSI
            os.close(fd)
        except Exception:
            try:
                os.close(fd)
            except Exception:
                pass
            return

        # ── Try running directly (succeeds when already admin) ────────────────
        try:
            r = subprocess.run(
                [bat_path], capture_output=True, timeout=8, shell=True
            )
            if r.returncode == 0:
                print(f"[Dashboard] Firewall configured for port {port}.")
                try:
                    os.unlink(bat_path)
                except Exception:
                    pass
                return
        except Exception:
            pass

        # ── ShellExecuteW: native UAC elevation (most reliable on Windows) ────
        # ShellExecuteW with verb "runas" always shows the UAC dialog regardless
        # of UAC level settings. Non-blocking — uvicorn is already running.
        print("[Dashboard] One-time network setup required.")
        print("[Dashboard] >>> A Windows security dialog will appear — click 'Yes' <<<")
        try:
            ret = ctypes.windll.shell32.ShellExecuteW(
                None,       # hwnd  (no parent window)
                "runas",    # verb  (request elevation)
                bat_path,   # file  (our .bat)
                None,       # params
                None,       # working dir
                0,          # SW_HIDE (run without a visible cmd window)
            )
            if int(ret) > 32:
                # ShellExecuteW returns immediately; bat finishes in ~1 second.
                # Sleep briefly so the rules are in place before the first retry.
                time.sleep(2)
                print(f"[Dashboard] Network setup complete — port {port} is open.")
                print("[Dashboard] Refresh your phone browser to connect.")
            else:
                print("[Dashboard] Setup was not allowed.")
                print("[Dashboard] Phone connections may fail until SHIRAZI is run as Administrator.")
        except Exception as e:
            print(f"[Dashboard] Firewall setup error: {e}")
        finally:
            # Cleanup after the bat has had time to run
            def _cleanup(path: str) -> None:
                time.sleep(5)
                try:
                    os.unlink(path)
                except Exception:
                    pass
            threading.Thread(target=_cleanup, args=(bat_path,), daemon=True).start()
        return

    # ── macOS ─────────────────────────────────────────────────────────────────
    if sys.platform == "darwin":
        fw_ctl = "/usr/libexec/ApplicationFirewall/socketfilterfw"
        try:
            r = subprocess.run(
                [fw_ctl, "--getglobalstate"], capture_output=True, text=True, timeout=5,
            )
            if "disabled" in r.stdout.lower():
                return  # firewall off — nothing to do

            py = sys.executable
            listed = subprocess.run(
                [fw_ctl, "--listapps"], capture_output=True, text=True, timeout=5,
            )
            if py in listed.stdout:
                return  # already allowed

            print("[Dashboard] One-time network setup — enter your password in the macOS dialog.")
            subprocess.run(
                ["osascript", "-e",
                 f'do shell script "{fw_ctl} --add {py} && {fw_ctl} --unblockapp {py}"'
                 f' with administrator privileges'],
                timeout=60,
            )
        except Exception:
            pass  # macOS firewall is off by default — silent failure is fine
        return

    # ── Linux ─────────────────────────────────────────────────────────────────
    def _privileged(cmd: list[str]) -> bool:
        for prefix in (["pkexec"], ["sudo", "-n"]):
            try:
                r = subprocess.run(prefix + cmd, capture_output=True, timeout=30)
                if r.returncode == 0:
                    return True
            except Exception:
                pass
        return False

    try:  # ufw
        r = subprocess.run(["ufw", "status"], capture_output=True, text=True, timeout=5)
        if "active" in r.stdout.lower():
            if _privileged(["ufw", "allow", f"{port}/tcp"]):
                print(f"[Dashboard] ufw: port {port} allowed.")
            else:
                print(f"[Dashboard] Run manually:  sudo ufw allow {port}/tcp")
            return
    except FileNotFoundError:
        pass

    try:  # firewalld
        r = subprocess.run(
            ["firewall-cmd", "--state"], capture_output=True, text=True, timeout=5,
        )
        if "running" in r.stdout.lower():
            ok = (_privileged(["firewall-cmd", "--add-port", f"{port}/tcp", "--permanent"])
                  and _privileged(["firewall-cmd", "--reload"]))
            if ok:
                print(f"[Dashboard] firewalld: port {port} allowed.")
            else:
                print(f"[Dashboard] Run manually:  sudo firewall-cmd --add-port={port}/tcp --permanent && sudo firewall-cmd --reload")
            return
    except FileNotFoundError:
        pass

    try:  # iptables (not persistent but works until reboot)
        r = subprocess.run(["iptables", "-L", "INPUT", "-n"], capture_output=True, timeout=5)
        if r.returncode == 0:
            if _privileged(["iptables", "-A", "INPUT", "-p", "tcp", "--dport", str(port), "-j", "ACCEPT"]):
                print(f"[Dashboard] iptables: port {port} opened.")
            else:
                print(f"[Dashboard] Run manually:  sudo iptables -A INPUT -p tcp --dport {port} -j ACCEPT")
    except FileNotFoundError:
        pass  # no iptables means firewall is probably off — nothing to do


def _ensure_crypto_js() -> None:
    if _CRYPTOJS_FILE.exists():
        return
    try:
        import urllib.request
        print("[Dashboard] Downloading CryptoJS (one-time setup)…")
        urllib.request.urlretrieve(_CRYPTOJS_CDN, str(_CRYPTOJS_FILE))
        print("[Dashboard] CryptoJS cached — will serve locally from now on.")
    except Exception as e:
        print(f"[Dashboard] CryptoJS download failed: {e}")
        print(f"[Dashboard] Encryption will fall back to CDN load on client.")


_ensure_crypto_js()


# ── helpers ───────────────────────────────────────────────────────────────────

def _local_ip() -> str:
    """Return the best LAN-facing IPv4 address, no internet required."""
    # Method 1: route trick (fast, works when internet is available)
    for probe in ("8.8.8.8", "1.1.1.1", "192.168.1.1"):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.5)
            s.connect((probe, 80))
            ip = s.getsockname()[0]
            s.close()
            if not ip.startswith("127."):
                return ip
        except Exception:
            pass

    # Method 2: hostname resolution (works offline on most systems)
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if not ip.startswith("127."):
            return ip
    except Exception:
        pass

    # Method 3: enumerate all interfaces (fully offline, no external deps)
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and not ip.startswith("169.254."):
                return ip
    except Exception:
        pass

    return "127.0.0.1"


def _cert_pair() -> tuple:
    """Return the (key_path, crt_path) the dashboard should use, or (None, None).

    Prefers the Shirazi pair; falls back to the legacy Mark-LIV pair so
    already-paired phones keep trusting the same certificate.
    """
    certs = BASE_DIR / "config" / "certs"
    key_p = certs / "shirazi.key"
    crt_p = certs / "shirazi.crt"
    if key_p.exists() and crt_p.exists():
        return key_p, crt_p
    legacy_key = certs / "jarvis.key"
    legacy_crt = certs / "jarvis.crt"
    if legacy_key.exists() and legacy_crt.exists():
        return legacy_key, legacy_crt
    return None, None


def _ensure_certs() -> bool:
    """
    Make sure config/certs holds a TLS key pair, generating a self-signed one the
    first time the dashboard runs.

    The pair is deliberately NOT shipped in the repository. A private key that
    every user downloads is the same as having no private key at all: anyone can
    present a certificate that matches it. Generating locally gives each install
    its own key, costs about a second, and happens exactly once.

    Returns True when a usable pair exists afterwards; False leaves the caller on
    plain HTTP, which still works — the QR code simply encodes http:// instead.
    """
    certs = BASE_DIR / "config" / "certs"
    key_p = certs / "shirazi.key"
    crt_p = certs / "shirazi.crt"
    if key_p.exists() and crt_p.exists():
        return True
    # Legacy Mark-LIV pair: reuse it if present so already-paired phones keep
    # trusting the same certificate; only generate fresh when nothing exists.
    if _cert_pair()[0] is not None:
        legacy = _cert_pair()
        if legacy[0].name == "jarvis.key":
            print("[Dashboard] Reusing legacy Mark-LIV TLS pair (docs/LEGACY_COMPAT.md).")
        return True

    try:
        import datetime
        import ipaddress
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError:
        print("[Dashboard] cryptography not installed — serving over plain HTTP.")
        print("[Dashboard] For HTTPS run:  pip install cryptography")
        return False

    try:
        certs.mkdir(parents=True, exist_ok=True)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

        who = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "SHIRAZI Dashboard"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "SHIRAZI"),
        ])

        # The SAN has to cover every address the phone might use: the LAN IP the
        # QR code encodes, plus localhost when testing on the machine itself.
        alt = [x509.DNSName("localhost"),
               x509.IPAddress(ipaddress.IPv4Address("127.0.0.1"))]
        try:
            lan = _local_ip()
            if not lan.startswith("127."):
                alt.append(x509.IPAddress(ipaddress.IPv4Address(lan)))
        except Exception:
            pass          # no LAN address resolvable — localhost entries still work

        # Timezone-aware UTC: datetime.utcnow() is deprecated from Python 3.12 on,
        # and the builder normalises aware values to UTC itself.
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(who)
            .issuer_name(who)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(x509.SubjectAlternativeName(alt), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(key, hashes.SHA256())
        )

        key_p.write_bytes(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))
        crt_p.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

        try:
            import os as _os
            _os.chmod(key_p, 0o600)   # best effort — largely a no-op on Windows
        except Exception:
            pass

        print(f"[Dashboard] Generated a self-signed certificate for this machine: {certs}")
        return True
    except Exception as e:
        print(f"[Dashboard] Certificate generation failed ({e}) — serving over plain HTTP.")
        return False


def _read(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


# ── DashboardServer ───────────────────────────────────────────────────────────

class DashboardServer:

    def __init__(self, account_store=None):
        """account_store: optional core.accounts.AccountStore (tests /
        embedders). When omitted, the default config/shirazi_accounts.db
        store is used and the "local" single-user account is ensured."""
        self._injected_store = account_store
        self._ip                          = _local_ip()
        self._tokens: set[str]            = set()
        self._token_keys: dict[str, str]  = {}   # auth_token → session_key
        self._aes_cache:  dict[str, bytes]= {}   # session_key → AES bytes
        self._clients: set[WebSocket]     = set()
        self._history: list[dict]         = []
        self._command_queue               = asyncio.Queue()
        self._wake_callback               = None
        self._connect_callback            = None
        self._pending_keys: dict[str, float] = {}
        self._pin_attempts: dict[str, list] = {}  # client_ip → [fails, first_ts, locked_until]
        self._device_sessions: dict[str, dict] = {}  # device_token → {session_key}
        self._phone_audio_queue: asyncio.Queue    = asyncio.Queue(maxsize=200)
        self._uploads_dir                 = UPLOADS_DIR
        self._login_html                  = _read("login.html")
        self._app_html                    = _read("app.html")
        # -- Phase 8: SaaS sessions -- the general mechanism. The dashboard's
        # bearer-token auth is the single-user instance of it: every token
        # minted below is adopted into the SessionManager for the "local"
        # admin user, gaining expiry/revocation/persistence. The legacy
        # in-memory sets stay as a compat fallback and are consulted first,
        # so the existing single-user flow behaves exactly as before.
        # Guarded: the dashboard must boot even if the accounts layer fails.
        self._accounts = None
        self._sessions = None
        self._usage = None
        self._local_user_id = "local"
        try:
            from core.accounts import (AccountStore, SessionManager,
                                       UsageTracker, ensure_local_user)
            from core.accounts.usage import install_registry_recorder
            self._accounts = (self._injected_store
                                if self._injected_store is not None
                                else AccountStore())
            self._local_user_id = ensure_local_user(self._accounts)
            self._sessions = SessionManager(self._accounts)
            self._usage = UsageTracker(self._accounts)
            try:
                install_registry_recorder(self._accounts, self._local_user_id)
            except Exception:
                pass
        except Exception as e:
            print(f"[Dashboard] accounts layer unavailable ({e}) -- "
                  f"in-memory auth only")
        self.app                          = self._build_app()
        # ── Phase 7: mobile dashboard state ─────────────────────────────
        self._agent             = None   # set_agent() by main.py
        self._agent_lock        = threading.Lock()  # serialises dashboard agent calls
        self._device_manager    = None   # Phase-5 DeviceManager (optional)
        self._backend_status_fn = None   # () -> {"live": bool} (optional)
        self._telemetry         = None   # lazy core.telemetry.TelemetrySampler
        self._loop              = None   # uvicorn loop, captured in serve()
        self._pending_confirms: dict = {}  # cid -> {event, approved, tool, args, why}
        self._pending_agent_runs: dict = {}  # rid -> parked gated step
        self._cmd_hits: dict = {}   # token -> [timestamps] (command rate limit)
        self._pad_hits: dict = {}   # token -> [timestamps] (touchpad rate limit)
        self._cmd_clients: set = set()  # /ws/cmd sockets (for targeted msgs)
        self._cursor = {"x": 960, "y": 540}  # touchpad cursor estimate
        self._quick_path = BASE_DIR / "config" / "dashboard.json"

    # ── PIN rate limiting (Phase 2) ─────────────────────────────────────────

    @staticmethod
    def _client_ip(req) -> str:
        try:
            return req.client.host if req.client else "unknown"
        except Exception:
            return "unknown"

    def _pin_lock_remaining(self, ip: str) -> float:
        """Seconds of lockout remaining for this IP, or 0.0 if not locked."""
        rec = self._pin_attempts.get(ip)
        if not rec:
            return 0.0
        now = time.time()
        if now >= rec[2]:
            return 0.0
        return rec[2] - now

    def _pin_failed(self, ip: str) -> None:
        """Record a failed PIN attempt; locks the IP out when over the limit."""
        now = time.time()
        # prune stale entries so the dict stays bounded
        self._pin_attempts = {
            k: v for k, v in self._pin_attempts.items()
            if now < v[2] or now - v[1] <= PIN_ATTEMPT_WINDOW
        }
        rec = self._pin_attempts.get(ip)
        if rec and now - rec[1] > PIN_ATTEMPT_WINDOW:
            rec = None  # stale window: start over
        fails = (rec[0] if rec else 0) + 1
        first_ts = rec[1] if rec else now
        locked_until = rec[2] if rec else 0.0
        if fails >= PIN_MAX_ATTEMPTS:
            locked_until = now + PIN_LOCKOUT_SECS
        self._pin_attempts[ip] = [fails, first_ts, locked_until]

    def _pin_ok(self, ip: str) -> None:
        """A successful login clears this IP's failure counter."""
        self._pin_attempts.pop(ip, None)

    # ── one-time key management ───────────────────────────────────────────
    # ── Phase 7: wiring (called by main.py) ───────────────────────────────

    def set_agent(self, agent) -> None:
        """Attach the Phase-6 agent engine. /api/agent and /ws/cmd route
        through it with per-call confirm hooks — the agent's own configured
        hook (used by the Live loop) is never mutated."""
        self._agent = agent

    def set_device_manager(self, mgr) -> None:
        self._device_manager = mgr

    def set_backend_status_fn(self, fn) -> None:
        """fn() -> dict, e.g. {"live": bool(session), "awake": bool}. Used by
        /api/session so the phone can show an honest backend state."""
        self._backend_status_fn = fn

    # ── Phase 7: origin validation (CSRF) ─────────────────────────────────

    def _origin_ok(self, headers) -> bool:
        """Same-host origins and non-browser clients pass. A cross-site page
        cannot hold a bearer token (no cookies are used), so this is
        belt-and-braces on top of token auth — mainly for the websocket
        handshake and the pre-auth /login endpoint."""
        origin = ""
        try:
            origin = (headers.get("origin") or "").strip()
        except Exception:
            pass
        if not origin:
            return True
        try:
            o_host = (urllib.parse.urlparse(origin).hostname or "").lower()
            h_host = (headers.get("host") or "").split(":")[0].lower()
        except Exception:
            return False
        return o_host in (h_host, self._ip.lower(), "localhost", "127.0.0.1")

    # ── Phase 7: command rate limiting ────────────────────────────────────

    def _rate_ok(self, token: str, kind: str) -> bool:
        """Sliding-window limiter. kind 'cmd' = 120/60s, 'move' = 40/s."""
        now = time.time()
        if kind == "move":
            store, limit, window = self._pad_hits, PAD_MOVE_RATE_MAX, 1.0
        else:
            store, limit, window = self._cmd_hits, CMD_RATE_MAX, CMD_RATE_WINDOW
        hits = [t for t in store.get(token, []) if now - t < window]
        if len(hits) >= limit:
            store[token] = hits
            return False
        hits.append(now)
        store[token] = hits
        # bound the dict
        if len(store) > 200:
            for k in list(store)[:100]:
                store.pop(k, None)
        return True

    # ── Phase 7: phone confirmation flow ──────────────────────────────────

    def _emit_threadsafe(self, msg: dict) -> None:
        """Broadcast from any thread (the confirm hook runs on agent pool
        threads, not the uvicorn loop)."""
        try:
            loop = self._loop
            if loop is not None and loop.is_running():
                asyncio.run_coroutine_threadsafe(self.broadcast(msg), loop)
        except Exception:
            pass

    def _phone_confirm_hook(self, tool: str, args: dict, reason: str) -> bool:
        return self.request_phone_confirmation(tool, args, reason)

    def request_phone_confirmation(self, tool: str, args: dict, reason: str,
                                   timeout_s: float = CONFIRM_TIMEOUT_SECS) -> bool:
        """Ask every connected phone to Approve/Deny a gated agent step.
        Blocks the calling (agent pool) thread until the phone answers or
        the timeout elapses. Returns False when nobody can be asked."""
        if not self._clients and not self._cmd_clients:
            return False
        cid = secrets.token_urlsafe(8)
        ev = threading.Event()
        self._pending_confirms[cid] = {
            "event": ev, "approved": None, "tool": tool,
            "args": dict(args or {}), "why": reason,
            "expires": time.time() + timeout_s,
        }
        self._emit_threadsafe({
            "type": "confirm_request", "id": cid, "tool": tool,
            "args": _summarise_args(dict(args or {})),
            "why": reason, "timeout_s": timeout_s,
        })
        answered = ev.wait(timeout_s)
        rec = self._pending_confirms.pop(cid, None)
        return bool(answered and rec and rec.get("approved") is True)

    def _resolve_confirm(self, cid: str, approved: bool) -> bool:
        rec = self._pending_confirms.get(cid)
        if not rec:
            return False
        rec["approved"] = bool(approved)
        rec["event"].set()
        return True

    # ── Phase 7: agent helpers ────────────────────────────────────────────

    def _agent_call(self, fn, *a, **k):
        """Run an agent call on a worker thread, serialised with the
        dashboard agent lock (two phones must not interleave confirm flows).
        The uvicorn loop thread never blocks: the lock is taken inside the
        worker."""
        def _do():
            with self._agent_lock:
                return fn(*a, **k)
        return _do

    async def _run_agent_text(self, text: str):
        agent = self._agent
        if agent is None:
            return None
        return await asyncio.to_thread(
            self._agent_call(agent.run_sync, text,
                             confirm_hook=self._phone_confirm_hook))

    async def _run_remote_tool(self, name: str, args: dict) -> dict:
        """Execute ONE allowlisted tool for the authenticated command channel.
        The permission engine validates every call: unknown tools fail closed,
        PRIVILEGED is denied. The phone tap on a PIN-paired device counts as
        the human approval for USER_CONFIRMATION-level remote-control tools —
        see the module security-model note."""
        from core import permissions
        from core.permissions import Level
        agent = self._agent
        if agent is None:
            return {"ok": False,
                    "error": "Agent engine unavailable on this desktop."}
        gate = permissions.check(name, args)
        if not gate.allowed:
            return {"ok": False, "error": f"Denied: {gate.reason}"}
        if gate.level is Level.PRIVILEGED:
            return {"ok": False, "error": "Denied: privileged tool."}
        try:
            out = await asyncio.to_thread(
                self._agent_call(agent.execute_tool, name, args,
                                 confirm_hook=lambda *a: True))
            return {"ok": True, "result": str(out)[:500]}
        except Exception as e:
            return {"ok": False, "error": f"Tool failed: {e}"}

    # ── Phase 7: touchpad cursor estimate ─────────────────────────────────

    def _screen_size(self) -> tuple:
        if _pag is not None:
            try:
                s = _pag.size()
                return (int(s[0]), int(s[1]))
            except Exception:
                pass
        return (1920, 1080)

    def _resync_cursor(self) -> bool:
        """Snap the estimate to the real pointer when pyautogui can read it."""
        if _pag is None:
            return False
        try:
            x, y = _pag.position()
            self._cursor = {"x": int(x), "y": int(y)}
            return True
        except Exception:
            return False

    # ── Phase 7: quick commands config ────────────────────────────────────

    def _quick_commands(self) -> list:
        cmds = [dict(c) for c in DEFAULT_QUICK_COMMANDS]
        try:
            data = json.loads(self._quick_path.read_text(encoding="utf-8"))
            user = data.get("quick_commands") if isinstance(data, dict) else None
            if isinstance(user, list):
                by_id = {c["id"]: c for c in cmds}
                for u in user:
                    if (isinstance(u, dict) and isinstance(u.get("id"), str)
                            and isinstance(u.get("prompt"), str)):
                        entry = {"id": u["id"][:32],
                                 "label": str(u.get("label") or u["id"])[:32],
                                 "icon": str(u.get("icon") or "\u2753")[:8],
                                 "prompt": u["prompt"][:500]}
                        by_id[entry["id"]] = entry
                cmds = [by_id[c["id"]] for c in cmds if c["id"] in by_id]
                cmds += [v for k, v in by_id.items()
                         if k not in {c["id"] for c in DEFAULT_QUICK_COMMANDS}]
        except Exception:
            pass
        return cmds

    def _save_quick_commands(self, commands: list) -> None:
        clean = []
        for u in commands:
            if not (isinstance(u, dict) and isinstance(u.get("id"), str)
                    and isinstance(u.get("prompt"), str)):
                continue
            if not re.fullmatch(r"[a-z0-9_\-]{1,32}", u["id"]):
                continue
            clean.append({"id": u["id"],
                          "label": str(u.get("label") or u["id"])[:32],
                          "icon": str(u.get("icon") or "\u2753")[:8],
                          "prompt": u["prompt"][:500]})
        self._quick_path.parent.mkdir(parents=True, exist_ok=True)
        self._quick_path.write_text(
            json.dumps({"quick_commands": clean}, indent=2,
                       ensure_ascii=False), encoding="utf-8")

    def _backend_status(self) -> dict:
        if self._backend_status_fn is None:
            return {"live": None, "note": "desktop did not report status"}
        try:
            st = self._backend_status_fn() or {}
            return {"live": st.get("live"), "awake": st.get("awake")}
        except Exception:
            return {"live": None, "note": "status probe failed"}


    def new_key(self, expiry_secs: int = 600) -> str:
        now = time.time()
        self._pending_keys = {k: v for k, v in self._pending_keys.items() if v > now}
        key = ''.join(secrets.choice(_KEY_CHARS) for _ in range(6))
        self._pending_keys[key] = now + expiry_secs
        return key

    @staticmethod
    def _ssl_enabled() -> bool:
        return _cert_pair()[0] is not None

    def get_url(self) -> str:
        proto = "https" if self._ssl_enabled() else "http"
        return f"{proto}://{self._ip}:{PORT}"

    def get_manual_url(self) -> str:
        """URL for manual browser entry. When HTTPS active, points to alias port (also HTTPS)."""
        if self._ssl_enabled():
            return f"{self._ip}:{PORT + 1}"
        return f"{self._ip}:{PORT}"

    def _aes_key(self, session_key: str) -> bytes:
        if session_key not in self._aes_cache:
            self._aes_cache[session_key] = _derive_key(session_key)
        return self._aes_cache[session_key]

    def _decrypt(self, token: str, enc_b64: str) -> str | None:
        sk = self._token_keys.get(token)
        if not sk and self._sessions is not None:
            # Phase 8: token adopted into the session manager (e.g. after a
            # restart the in-memory map is empty but the session persists).
            try:
                sk = self._sessions.session_key_for(token)
            except Exception:
                sk = None
        if not sk:
            return None
        try:
            return _decrypt_cbc(self._aes_key(sk), enc_b64)
        except Exception:
            return None

    # -- Phase 8: general session mechanism (single-user instance) --------
    def _session_valid(self, token: str) -> bool:
        """True when the token is a live session (general mechanism)."""
        if self._sessions is None or not token:
            return False
        try:
            return self._sessions.validate(token) is not None
        except Exception:
            return False

    def _register_session(self, token: str, session_key: str = "",
                          *, label: str = "", kind: str = "bearer",
                          device_id: "str | None" = None) -> None:
        """Adopt a freshly-minted bearer into the session manager. The
        legacy in-memory set keeps working alongside (compat fallback)."""
        if self._sessions is None:
            return
        try:
            self._sessions.adopt(self._local_user_id, token,
                                 session_key=session_key or "",
                                 label=label, kind=kind,
                                 device_id=device_id)
        except Exception:
            pass

    def _auth_token(self, token: str):
        """Validate a bearer token -> user_id (or None). Used by /api/v1."""
        token = (token or "").strip()
        if not token:
            return None
        if token in self._tokens:
            return self._local_user_id
        if self._sessions is not None:
            try:
                s = self._sessions.validate(token)
                if s:
                    return s["user_id"]
            except Exception:
                pass
        return None

    def _purge_device_bearers(self, session_keys) -> int:
        """Drop legacy in-memory bearer tokens minted from revoked devices.

        Phase 9 hardening: `_auth` consults the in-memory `_tokens` set
        *before* the SessionManager, so revoking a device's session records
        alone would leave its already-minted bearer tokens working. Bearer
        tokens minted via /api/device-login carry the device's channel key
        in `_token_keys`; purging by that key restores the cascade.
        """
        keys = {k for k in session_keys if k}
        if not keys:
            return 0
        doomed = [t for t in list(self._tokens)
                  if self._token_keys.get(t) in keys]
        for t in doomed:
            self._tokens.discard(t)
            self._token_keys.pop(t, None)
        return len(doomed)

    # ── callbacks ────────────────────────────────────────────────────────

    def set_wake_callback(self, fn) -> None:
        self._wake_callback = fn

    def set_connect_callback(self, fn) -> None:
        self._connect_callback = fn

    # ── broadcast ────────────────────────────────────────────────────────

    async def broadcast(self, msg: dict) -> None:
        # Refresh the loop handle on every broadcast: the confirm hook emits
        # from agent pool threads via run_coroutine_threadsafe, and this keeps
        # the handle valid even if the server object outlives a loop restart.
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        self._history.append(msg)
        if len(self._history) > 300:
            self._history = self._history[-300:]
        dead: set[WebSocket] = set()
        for ws in list(self._clients):
            try:
                await ws.send_json(msg)
            except Exception:
                dead.add(ws)
        self._clients -= dead

    # ── FastAPI app ───────────────────────────────────────────────────────

    def _build_app(self) -> "FastAPI":
        app = FastAPI(docs_url=None, redoc_url=None)

        def _auth(req: Request) -> bool:
            tok = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
            if not tok:
                return False
            # Legacy in-memory set first (compat), then the general
            # session mechanism (Phase 8).
            return tok in self._tokens or self._session_valid(tok)

        # Phase 8: versioned SaaS API surface (/api/v1/...)
        try:
            from dashboard.api_v1 import register_api_v1
            register_api_v1(app, self)
        except Exception as e:
            print(f"[Dashboard] /api/v1 unavailable ({e})")

        # serve CryptoJS from local cache, fallback to CDN redirect
        @app.get("/static/crypto.js")
        async def serve_crypto():
            if _CRYPTOJS_FILE.exists():
                return FileResponse(str(_CRYPTOJS_FILE),
                                    media_type="application/javascript")
            from fastapi.responses import RedirectResponse
            return RedirectResponse(_CRYPTOJS_CDN)

        @app.get("/login", response_class=HTMLResponse)
        async def login_page():
            return HTMLResponse(self._login_html)

        @app.get("/", response_class=HTMLResponse)
        async def index():
            # Auth is handled client-side via sessionStorage bearer token.
            # Server-side header auth can't work here because browser navigations
            # don't send custom headers (location.href doesn't carry Authorization).
            html = (self._app_html
                    .replace("__IP__", self._ip)
                    .replace("__PORT__", str(PORT)))
            return HTMLResponse(html)

        @app.post("/login")
        async def login(req: Request):
            body    = await req.json()
            entered = str(body.get("pin", "")).strip().upper()
            now     = time.time()
            ip      = self._client_ip(req)
            locked  = self._pin_lock_remaining(ip)
            if locked > 0:
                return JSONResponse(
                    {"ok": False, "error": f"Too many attempts. Try again in {int(locked)}s."},
                    status_code=429)
            if entered in self._pending_keys and self._pending_keys[entered] > now:
                del self._pending_keys[entered]          # one-time use
                self._pin_ok(ip)                          # success clears the counter
                tok = secrets.token_urlsafe(32)
                self._tokens.add(tok)
                self._token_keys[tok] = entered
                self._aes_key(entered)                   # pre-derive & cache
                self._register_session(tok, entered, label="pin-login")
                if self._connect_callback:
                    self._connect_callback()
                asyncio.create_task(self.broadcast(
                    {"type": "sys", "text": "Remote connection established."}
                ))
                # Bearer token in response body — no cookies needed (works on any browser/HTTP)
                return JSONResponse({"ok": True, "token": tok})
            self._pin_failed(ip)
            return JSONResponse({"ok": False, "error": "Invalid or expired key"},
                                status_code=401)

        @app.get("/auto-login")
        async def auto_login(key: str = "", req: Request = None):
            """QR code target — validates one-time key, creates session, redirects phone."""
            now = time.time()
            ip = self._client_ip(req) if req is not None else "unknown"
            locked = self._pin_lock_remaining(ip)
            if locked > 0:
                return HTMLResponse(
                    f"<html><body><h2>Too Many Attempts</h2>"
                    f"<p>Try again in {int(locked)}s.</p></body></html>",
                    status_code=429)
            if not key or key not in self._pending_keys or self._pending_keys[key] <= now:
                self._pin_failed(ip)
                return HTMLResponse("""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width">
<style>
  body{background:#07090f;color:#dde3ed;font-family:sans-serif;
       display:flex;align-items:center;justify-content:center;height:100vh;margin:0;text-align:center}
  h2{color:#f87171;margin-bottom:12px}p{color:#5e6a7e;font-size:14px}
</style></head>
<body><div><h2>Link Expired</h2>
<p>Press <strong style="color:#dde3ed">Remote Control</strong> in SHIRAZI to get a new QR code.</p>
</div></body></html>""")

            del self._pending_keys[key]
            self._pin_ok(ip)                              # success clears the counter
            tok     = secrets.token_urlsafe(32)
            dev_tok = secrets.token_urlsafe(32)
            self._tokens.add(tok)
            self._token_keys[tok] = key
            self._aes_key(key)
            self._device_sessions[dev_tok] = {"session_key": key}
            # Phase 8: the same credentials in the general mechanism.
            self._register_session(tok, key, label="qr-pairing")
            if self._sessions is not None:
                try:
                    self._sessions.register_device_token(
                        self._local_user_id, dev_tok, name="phone",
                        kind="phone", session_key=key)
                except Exception:
                    pass

            if self._connect_callback:
                self._connect_callback()
            asyncio.create_task(self.broadcast(
                {"type": "sys", "text": "Remote connection established via QR code."}
            ))

            return HTMLResponse(f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width">
<style>
  body{{background:#07090f;color:#dde3ed;font-family:sans-serif;
       display:flex;align-items:center;justify-content:center;height:100vh;margin:0;text-align:center}}
  p{{color:#5e6a7e;font-size:14px}}
</style></head>
<body>
<script>
  sessionStorage.setItem('shirazi_token','{tok}');
  sessionStorage.setItem('shirazi_key','{key}');
  localStorage.setItem('shirazi_device_token','{dev_tok}');
  setTimeout(function(){{location.replace('/')}},400);
</script>
<p>Connecting to SHIRAZI…</p>
</body></html>""")

        @app.post("/api/device-login")
        async def device_login_ep(req: Request):
            """Return a fresh auth token for a previously paired device token."""
            try:
                body = await req.json()
            except Exception:
                return JSONResponse({"ok": False}, status_code=400)
            ip = self._client_ip(req)
            locked = self._pin_lock_remaining(ip)
            if locked > 0:
                return JSONResponse(
                    {"ok": False,
                     "error": f"Too many attempts. Try again in {int(locked)}s."},
                    status_code=429)
            dev_tok = (body.get("device_token") or "").strip()
            rec = self._device_sessions.get(dev_tok) if dev_tok else None
            session_key = rec["session_key"] if rec else None
            if session_key is None and self._sessions is not None:
                # Phase 8: device registry survives restarts (the in-memory
                # map above does not). Legacy jarvis_device_token values
                # validate by token *value* either way (LEGACY_COMPAT §4).
                try:
                    session_key = self._sessions.device_session_key(dev_tok)
                    if session_key:
                        self._device_sessions[dev_tok] = {
                            "session_key": session_key}
                        self._sessions.touch_device(dev_tok)
                except Exception:
                    session_key = None
            if not dev_tok or not session_key:
                return JSONResponse({"ok": False}, status_code=401)
                self._pin_failed(ip)
            tok = secrets.token_urlsafe(32)
            self._tokens.add(tok)
            self._token_keys[tok] = session_key
            self._aes_key(session_key)
            # Phase 9: link the bearer to its device so revoking the
            # device cascades to this session (revoke_device /
            # revoke_all_devices clear sessions by device_id).
            _dev_id = None
            if self._sessions is not None:
                try:
                    _d = self._sessions.find_device(dev_tok)
                    _dev_id = _d.id if _d else None
                except Exception:
                    _dev_id = None
            self._register_session(tok, session_key, label="device-login",
                                   device_id=_dev_id)
            if self._connect_callback:
                self._connect_callback()
            asyncio.create_task(self.broadcast(
                {"type": "sys", "text": "Known device reconnected automatically."}
            ))
            return JSONResponse({"ok": True, "token": tok, "key": session_key})

        @app.post("/api/revoke-devices")
        async def revoke_devices(req: Request):
            """Invalidate all persistent device tokens (admin action)."""
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            count = len(self._device_sessions)
            # Phase 9: cascade into the legacy in-memory bearer sets -- a
            # bearer minted via /api/device-login must die with its device.
            revoked_keys = {rec.get("session_key")
                            for rec in self._device_sessions.values()}
            self._device_sessions.clear()
            count += self._purge_device_bearers(revoked_keys)
            if self._sessions is not None:
                try:
                    count += self._sessions.revoke_all_devices(
                        self._local_user_id)
                except Exception:
                    pass
            return JSONResponse({"ok": True, "revoked": count})

        @app.post("/api/command")
        async def command(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            body  = await req.json()
            token = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
            enc   = body.get("enc", "")
            if not self._rate_ok(token, "cmd"):
                return JSONResponse({"error": "Rate limit exceeded"}, status_code=429)
            if enc:
                text = self._decrypt(token, enc)
                if not isinstance(text, str):
                    return JSONResponse({"error": "Decryption failed"}, status_code=400)
            else:
                # Legacy plaintext path: kept for old clients; the phone PWA
                # sends AES-256-CBC "enc" payloads. Strictly validated.
                text = body.get("text")
                text = text.strip() if isinstance(text, str) else ""
            if len(text) > 1000:
                return JSONResponse({"error": "Command too long"}, status_code=400)
            if text:
                await self._command_queue.put(text)
                if self._wake_callback:
                    self._wake_callback()
            return JSONResponse({"ok": True})

        @app.post("/api/wake")
        async def wake_ep(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            if self._wake_callback:
                self._wake_callback()
            return JSONResponse({"ok": True})

        # ── Phone mic real-time audio → Gemini Live ──────────────────────────

        @app.websocket("/ws/phone-audio")
        async def phone_audio_ws(websocket: WebSocket, token: str = ""):
            tok = token.strip()
            if not tok or tok not in self._tokens:
                await websocket.close(code=4001)
                return
            if not self._origin_ok(websocket.headers):
                await websocket.close(code=4001)
                return
            await websocket.accept()
            asyncio.create_task(self.broadcast(
                {"type": "sys", "text": "Phone microphone live."}
            ))
            try:
                while True:
                    data = await websocket.receive_bytes()
                    try:
                        self._phone_audio_queue.put_nowait(
                            {"data": data, "mime_type": "audio/pcm"}
                        )
                    except asyncio.QueueFull:
                        pass  # drop frame rather than block
            except WebSocketDisconnect:
                pass
            finally:
                asyncio.create_task(self.broadcast(
                    {"type": "sys", "text": "Phone microphone stopped."}
                ))

        # ── File sharing ──────────────────────────────────────────────────────

        def _safe_filename(raw: str) -> str:
            name = Path(raw).name                          # strip path components
            name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip(". ")
            return name or "upload"

        if _UPLOAD_OK:
            @app.post("/api/upload")
            async def upload_file(req: Request, file: UploadFile = FastAPIFile(...)):
                if not _auth(req):
                    return JSONResponse({"error": "Unauthorized"}, status_code=401)

                safe = _safe_filename(file.filename or "upload")
                dest = self._uploads_dir / safe
                stem, suffix = Path(safe).stem, Path(safe).suffix
                counter = 1
                while dest.exists():
                    dest = self._uploads_dir / f"{stem}_{counter}{suffix}"
                    counter += 1

                size = 0
                max_bytes = MAX_UPLOAD_MB * 1024 * 1024
                try:
                    with open(dest, "wb") as fout:
                        while True:
                            chunk = await file.read(65536)
                            if not chunk:
                                break
                            size += len(chunk)
                            if size > max_bytes:
                                fout.close()
                                dest.unlink(missing_ok=True)
                                return JSONResponse(
                                    {"error": f"File too large (max {MAX_UPLOAD_MB} MB)"},
                                    status_code=413,
                                )
                            fout.write(chunk)
                except Exception as exc:
                    try:
                        dest.unlink(missing_ok=True)
                    except Exception:
                        pass
                    return JSONResponse({"error": str(exc)}, status_code=500)

                asyncio.create_task(self.broadcast({
                    "type": "file_received",
                    "name": dest.name,
                    "size": size,
                    "saved_to": str(self._uploads_dir),
                }))
                return JSONResponse({"ok": True, "name": dest.name, "size": size})
        else:
            @app.post("/api/upload")
            async def upload_unavailable(req: Request):
                return JSONResponse(
                    {"error": "File uploads require: pip install python-multipart"},
                    status_code=503,
                )

        @app.get("/api/files")
        async def list_files(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            files = []
            try:
                for f in sorted(
                    (p for p in self._uploads_dir.iterdir() if p.is_file()),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                ):
                    files.append({"name": f.name, "size": f.stat().st_size})
            except Exception:
                pass
            return JSONResponse({"files": files})

        @app.get("/uploads/{filename}")
        async def download_file(filename: str, token: str = ""):
            # Auth via query param — browser <a download> can't send custom headers
            tok = token.strip()
            if not tok or tok not in self._tokens:
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            safe = re.sub(r'[/\\]', '', filename)
            path = self._uploads_dir / safe
            if not path.exists() or not path.is_file():
                return JSONResponse({"error": "Not found"}, status_code=404)
            return FileResponse(str(path), filename=safe)

        @app.websocket("/ws")
        async def ws_ep(websocket: WebSocket, token: str = ""):
            tok = token.strip()
            if not tok or tok not in self._tokens:
                await websocket.close(code=4001)
                return
            if not self._origin_ok(websocket.headers):
                await websocket.close(code=4001)
                return
            await websocket.accept()
            self._clients.add(websocket)
            for entry in self._history[-50:]:
                try:
                    await websocket.send_json(entry)
                except Exception:
                    break
            try:
                while True:
                    data = await websocket.receive_json()
                    if data.get("type") == "command":
                        enc = data.get("enc", "")
                        if enc:
                            t = self._decrypt(tok, enc)
                            t = t if isinstance(t, str) else None
                        else:
                            t = data.get("text")
                            t = t.strip() if isinstance(t, str) else None
                        if t and len(t) <= 1000:
                            await self._command_queue.put(t)
                            if self._wake_callback:
                                self._wake_callback()
            except WebSocketDisconnect:
                pass
            finally:
                self._clients.discard(websocket)

        # ── Phase 7: PWA + i18n static assets ────────────────────────────
        @app.get("/manifest.webmanifest")
        async def pwa_manifest():
            return FileResponse(str(STATIC_DIR / "manifest.webmanifest"),
                                media_type="application/manifest+json")

        @app.get("/sw.js")
        async def pwa_sw():
            return FileResponse(str(STATIC_DIR / "sw.js"),
                                media_type="application/javascript")

        @app.get("/icons/{name}")
        async def pwa_icon(name: str):
            safe = re.sub(r"[^a-z0-9_.-]", "", name)
            path = STATIC_DIR / "icons" / safe
            if not path.exists() or not path.is_file():
                return JSONResponse({"error": "Not found"}, status_code=404)
            return FileResponse(str(path), media_type="image/png")

        @app.get("/i18n/{lang}.json")
        async def dashboard_i18n(lang: str):
            if lang not in ("en", "ur", "ar", "ur-Latn"):
                return JSONResponse({"error": "Unknown language"},
                                    status_code=404)
            path = STATIC_DIR / "i18n" / f"{lang}.json"
            if not path.exists():
                return JSONResponse({"error": "Not found"}, status_code=404)
            return FileResponse(str(path), media_type="application/json")

        # ═════════ Phase 7: mobile dashboard routes ═════════

        @app.post("/api/agent")
        async def agent_route(req: Request):
            """Natural language → agent engine. Gated steps surface as
            Approve/Deny on the phone via the confirm flow; a timed-out
            gate is parked and returned as `pending` for late approval."""
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            try:
                body = await req.json()
            except Exception:
                return JSONResponse({"error": "Invalid JSON"}, status_code=400)
            text = str((body or {}).get("text", "")).strip()
            if not text:
                return JSONResponse({"error": "Field 'text' is required"},
                                    status_code=400)
            if len(text) > AGENT_TEXT_MAX:
                return JSONResponse(
                    {"error": f"Text too long (max {AGENT_TEXT_MAX} chars)"},
                    status_code=400)
            if self._agent is None:
                return JSONResponse(
                    {"error": "Agent engine is not available on this desktop. "
                              "Start SHIRAZI with the agent enabled."},
                    status_code=503)
            try:
                result = await self._run_agent_text(text)
            except Exception as e:
                return JSONResponse({"error": f"Agent run failed: {e}"},
                                    status_code=500)
            status = result.status.value
            resp = {"ok": True, "status": status, "answer": result.answer,
                    "steps_executed": result.steps_executed, "ms": result.ms,
                    "pending": None}
            if status == "cancelled" and result.pending:
                # Gate timed out with no answer: park it so the phone can
                # approve late via /api/agent/confirm.
                rid = secrets.token_urlsafe(8)
                self._pending_agent_runs[rid] = dict(result.pending)
                resp["pending"] = {"id": rid, "tool": result.pending.get("tool"),
                                   "args": result.pending.get("args"),
                                   "why": result.pending.get("why")}
            elif status == "pending_confirmation" and result.pending:
                rid = secrets.token_urlsafe(8)
                self._pending_agent_runs[rid] = dict(result.pending)
                resp["pending"] = {"id": rid, "tool": result.pending.get("tool"),
                                   "args": result.pending.get("args"),
                                   "why": result.pending.get("why")}
            return JSONResponse(resp)

        @app.post("/api/agent/confirm")
        async def agent_confirm_route(req: Request):
            """Resolve a parked gated step: {id, approved}. Runs ONLY the
            single parked tool with the phone's decision — never a new plan."""
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            try:
                body = await req.json()
            except Exception:
                return JSONResponse({"error": "Invalid JSON"}, status_code=400)
            rid = str((body or {}).get("id", ""))
            approved = bool((body or {}).get("approved"))
            parked = self._pending_agent_runs.pop(rid, None)
            if not parked:
                return JSONResponse(
                    {"error": "Unknown or expired confirmation id"},
                    status_code=404)
            if self._agent is None:
                return JSONResponse(
                    {"error": "Agent engine is not available"}, status_code=503)
            agent = self._agent
            try:
                out = await asyncio.to_thread(
                    self._agent_call(agent.execute_tool, parked["tool"],
                                     dict(parked.get("args") or {}),
                                     confirm_hook=lambda *a: approved))
            except Exception as e:
                return JSONResponse({"error": f"Tool failed: {e}"},
                                    status_code=500)
            return JSONResponse({"ok": True, "approved": approved,
                                 "tool": parked["tool"], "result": str(out)[:2000]})

        @app.get("/api/session")
        async def session_route(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            return JSONResponse({
                "ok": True,
                "agent_available": self._agent is not None,
                "device_manager": self._device_manager is not None,
                "pending_confirms": len(self._pending_confirms),
                "pending_agent_runs": len(self._pending_agent_runs),
                "backend": self._backend_status(),
            })

        @app.get("/api/telemetry")
        async def telemetry_route(req: Request):
            """Throttled system telemetry. The sampler caches for 2 s, so a
            fast phone poller can never turn into a fast psutil loop."""
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            try:
                from core.telemetry import TelemetrySampler
            except Exception as e:
                return JSONResponse({"error": f"telemetry unavailable: {e}"},
                                    status_code=503)
            if self._telemetry is None:
                self._telemetry = TelemetrySampler(min_interval=2.0)
            try:
                sample = await asyncio.to_thread(self._telemetry.snapshot)
            except Exception as e:
                return JSONResponse({"error": f"telemetry probe failed: {e}"},
                                    status_code=500)
            rows = [{"label": label, "value": value, "display": display}
                    for label, value, display in sample.as_rows()]
            return JSONResponse({"ok": True, "rows": rows})

        @app.get("/api/audio-devices")
        async def audio_devices_route(req: Request, rescan: bool = False):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            try:
                from core import audio_devices as ad
            except Exception as e:
                return JSONResponse({"available": False,
                                     "error": f"audio stack unavailable: {e}"})
            def _do():
                if rescan:
                    ad.rescan()
                ins = ad.list_devices("input")
                outs = ad.list_devices("output")
                sel_in = sel_out = ""
                if self._device_manager is not None:
                    try:
                        sel_in = self._device_manager.selected_input() or ""
                        sel_out = self._device_manager.selected_output() or ""
                    except Exception:
                        pass
                state = {}
                try:
                    state["input"] = ad.status_line(sel_in, "input")
                except Exception as e:
                    state["input"] = f"unavailable: {e}"
                try:
                    state["output"] = ad.status_line(sel_out, "output")
                except Exception as e:
                    state["output"] = f"unavailable: {e}"
                return {"available": True, "input": ins, "output": outs,
                        "selected": {"input": sel_in, "output": sel_out},
                        "state": state}
            try:
                return JSONResponse(await asyncio.to_thread(_do))
            except Exception as e:
                return JSONResponse({"available": False,
                                     "error": f"device query failed: {e}"})

        @app.post("/api/audio-devices")
        async def audio_devices_select(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            try:
                body = await req.json()
            except Exception:
                return JSONResponse({"error": "Invalid JSON"}, status_code=400)
            kind = str((body or {}).get("kind", "")).strip()
            name = str((body or {}).get("name", ""))
            if kind not in ("input", "output"):
                return JSONResponse({"error": "kind must be 'input' or 'output'"},
                                    status_code=400)
            if self._device_manager is None:
                return JSONResponse(
                    {"error": "Device manager is not available on this desktop"},
                    status_code=503)
            try:
                from core import audio_devices as ad
                valid = ad.list_devices(kind)
            except Exception as e:
                return JSONResponse({"error": f"audio stack unavailable: {e}"},
                                    status_code=503)
            if name and name not in valid:
                return JSONResponse({"error": "Unknown device name"},
                                    status_code=400)
            try:
                ok = (self._device_manager.select_input(name) if kind == "input"
                      else self._device_manager.select_output(name))
            except Exception as e:
                return JSONResponse({"error": f"selection failed: {e}"},
                                    status_code=500)
            return JSONResponse({"ok": bool(ok), "kind": kind, "name": name})

        @app.post("/api/audio-devices/rescan")
        async def audio_devices_rescan(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            try:
                from core import audio_devices as ad
                fresh = await asyncio.to_thread(ad.rescan)
                return JSONResponse({"ok": True, **fresh})
            except Exception as e:
                return JSONResponse({"error": f"rescan failed: {e}"},
                                    status_code=503)

        @app.post("/api/audio-devices/test")
        async def audio_devices_test(req: Request):
            """Play the synthetic test chime on the selected output device."""
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            try:
                from core import audio_devices as ad
            except Exception as e:
                return JSONResponse({"error": f"audio stack unavailable: {e}"},
                                    status_code=503)
            sel = ""
            if self._device_manager is not None:
                try:
                    sel = self._device_manager.selected_output() or ""
                except Exception:
                    pass
            msg = await asyncio.to_thread(ad.play_test_chime, sel)
            ok = msg.startswith("Test chime played")
            return JSONResponse({"ok": ok, "message": msg})

        @app.get("/api/quick-commands")
        async def quick_commands_list(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            return JSONResponse({"ok": True,
                                 "commands": self._quick_commands()})

        @app.post("/api/quick-commands")
        async def quick_commands_save(req: Request):
            """Replace the user quick-command set (validated, then persisted
            to config/dashboard.json). Defaults are never deleted — a saved
            entry with the same id overrides its default."""
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            try:
                body = await req.json()
            except Exception:
                return JSONResponse({"error": "Invalid JSON"}, status_code=400)
            commands = (body or {}).get("commands")
            if not isinstance(commands, list) or len(commands) > 64:
                return JSONResponse(
                    {"error": "commands must be a list (max 64)"},
                    status_code=400)
            try:
                self._save_quick_commands(commands)
            except Exception as e:
                return JSONResponse({"error": f"save failed: {e}"},
                                    status_code=500)
            return JSONResponse({"ok": True,
                                 "commands": self._quick_commands()})

        @app.post("/api/quick-commands/run")
        async def quick_commands_run(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            try:
                body = await req.json()
            except Exception:
                return JSONResponse({"error": "Invalid JSON"}, status_code=400)
            qid = str((body or {}).get("id", ""))
            cmd = next((c for c in self._quick_commands() if c["id"] == qid),
                       None)
            if cmd is None:
                return JSONResponse({"error": "Unknown quick command"},
                                    status_code=404)
            if self._agent is None:
                return JSONResponse(
                    {"error": "Agent engine is not available"}, status_code=503)
            try:
                result = await self._run_agent_text(cmd["prompt"])
            except Exception as e:
                return JSONResponse({"error": f"Agent run failed: {e}"},
                                    status_code=500)
            return JSONResponse({"ok": True, "id": qid,
                                 "status": result.status.value,
                                 "answer": result.answer})

        @app.websocket("/ws/cmd")
        async def cmd_ws(websocket: WebSocket, token: str = ""):
            """Authenticated command channel: D-pad, touchpad, volume,
            quick commands, and confirm responses. Every payload is
            allowlisted and validated — see _handle_cmd."""
            tok = token.strip()
            if not tok or tok not in self._tokens:
                await websocket.close(code=4001)
                return
            if not self._origin_ok(websocket.headers):
                await websocket.close(code=4003)
                return
            await websocket.accept()
            self._cmd_clients.add(websocket)
            await websocket.send_json({"type": "hello", "server": "shirazi",
                                       "v": 7})
            try:
                while True:
                    data = await websocket.receive_json()
                    if not isinstance(data, dict):
                        continue
                    await self._handle_cmd(tok, websocket, data)
            except WebSocketDisconnect:
                pass
            finally:
                self._cmd_clients.discard(websocket)


        return app
    # ── serve ─────────────────────────────────────────────────────────────
    # ── Phase 7: /ws/cmd message handling ───────────────────────────────

    async def _handle_cmd(self, tok: str, ws: WebSocket, data: dict) -> None:
        mtype = str(data.get("type", ""))

        if mtype == "ping":
            await ws.send_json({"type": "pong"})
            return

        if mtype == "confirm_response":
            cid = str(data.get("id", ""))
            approved = bool(data.get("approved"))
            ok = self._resolve_confirm(cid, approved)
            await ws.send_json({"type": "confirm_ack", "id": cid, "ok": ok})
            return

        # Touchpad moves are high-frequency: their own 40/s budget.
        if mtype == "touchpad_move":
            if not self._rate_ok(tok, "move"):
                return  # drop rather than queue — the next move supersedes
            await self._cmd_touchpad_move(ws, data)
            return

        if not self._rate_ok(tok, "cmd"):
            await ws.send_json({"type": "error",
                                "error": "Rate limit exceeded — slow down."})
            return

        if mtype == "touchpad_start":
            synced = self._resync_cursor()
            await ws.send_json({"type": "touchpad", "synced": synced,
                                "x": self._cursor["x"], "y": self._cursor["y"]})
        elif mtype == "touchpad_click":
            button = str(data.get("button", "left")).lower()
            if button not in ("left", "right"):
                await ws.send_json({"type": "error",
                                    "error": "button must be left|right"})
                return
            r = await self._run_remote_tool("mouse_click", {"button": button})
            await ws.send_json({"type": "click", "button": button, **r})
        elif mtype == "dpad":
            key = str(data.get("key", "")).lower()
            if key not in DPAD_KEYS:
                await ws.send_json({"type": "error",
                                    "error": f"unknown key: {key!r}"})
                return
            r = await self._run_remote_tool("keyboard_press", {"key": key})
            await ws.send_json({"type": "key", "key": key, **r})
        elif mtype == "scroll":
            direction = str(data.get("direction", "down")).lower()
            if direction not in ("up", "down", "left", "right"):
                await ws.send_json({"type": "error",
                                    "error": "direction must be up|down|left|right"})
                return
            try:
                amount = max(1, min(int(data.get("amount", 3) or 3), 20))
            except Exception:
                amount = 3
            r = await self._run_remote_tool(
                "scroll", {"direction": direction, "amount": amount})
            await ws.send_json({"type": "scroll", "direction": direction, **r})
        elif mtype == "volume":
            action = str(data.get("action", "")).lower()
            if action not in VOLUME_ACTIONS:
                await ws.send_json({"type": "error",
                                    "error": f"unknown volume action: {action!r}"})
                return
            tool = {"up": "volume_up", "down": "volume_down",
                    "mute": "volume_mute", "unmute": "volume_unmute",
                    "set": "volume_set"}[action]
            args = {}
            if action == "set":
                try:
                    args["level"] = max(0, min(int(data.get("level", 50)), 100))
                except Exception:
                    args["level"] = 50
            r = await self._run_remote_tool(tool, args)
            await ws.send_json({"type": "volume", "action": action, **r})
        elif mtype == "quick":
            qid = str(data.get("id", ""))
            cmd = next((c for c in self._quick_commands() if c["id"] == qid),
                       None)
            if cmd is None:
                await ws.send_json({"type": "error",
                                    "error": "unknown quick command"})
                return
            if self._agent is None:
                await ws.send_json({"type": "error",
                                    "error": "agent unavailable"})
                return
            try:
                result = await self._run_agent_text(cmd["prompt"])
                await ws.send_json({"type": "quick", "id": qid,
                                    "status": result.status.value,
                                    "answer": result.answer})
            except Exception as e:
                await ws.send_json({"type": "error",
                                    "error": f"agent failed: {e}"})
        else:
            await ws.send_json({"type": "error",
                                "error": f"unknown message type: {mtype!r}"})

    async def _cmd_touchpad_move(self, ws: WebSocket, data: dict) -> None:
        try:
            dx = float(data.get("dx", 0) or 0)
            dy = float(data.get("dy", 0) or 0)
            sens = float(data.get("sensitivity", 1.0) or 1.0)
        except Exception:
            return
        # Clamp: a single message may not fling the pointer across the planet.
        dx = max(-400, min(400, dx * max(0.1, min(5.0, sens))))
        dy = max(-400, min(400, dy * max(0.1, min(5.0, sens))))
        sw, sh = self._screen_size()
        self._cursor["x"] = int(max(0, min(sw - 1, self._cursor["x"] + dx)))
        self._cursor["y"] = int(max(0, min(sh - 1, self._cursor["y"] + dy)))
        r = await self._run_remote_tool(
            "mouse_move", {"x": self._cursor["x"], "y": self._cursor["y"]})
        if not r.get("ok"):
            await ws.send_json({"type": "error", "error": r.get("error")})

    # ── serve ─────────────────────────────────────────────────────────────

    async def _serve_alias(self) -> None:
        """Second HTTPS server on PORT+1 sharing the same app and in-memory state.
        Chrome HTTPS-upgrades any bare IP:PORT the user types, so this port also needs TLS.
        User types IP:8001 → Chrome tries https → self-signed cert warning → accept once → done."""
        ssl_key, ssl_cert = _cert_pair()
        asyncio.get_event_loop().run_in_executor(None, _ensure_network_access, PORT + 1)
        cfg = uvicorn.Config(
            self.app, host="0.0.0.0", port=PORT + 1, log_level="warning",
            ssl_keyfile=str(ssl_key), ssl_certfile=str(ssl_cert),
        )
        print(f"[Dashboard] Manual entry:  {self._ip}:{PORT + 1}  (type in browser, accept cert once)")
        await uvicorn.Server(cfg).serve()

    async def serve(self) -> None:
        if not _DEPS_OK:
            print("[Dashboard] fastapi/uvicorn not installed — dashboard disabled.")
            print("[Dashboard] Run:  pip install fastapi 'uvicorn[standard]' cryptography")
            return

        # Firewall setup runs in a thread — uvicorn starts immediately,
        self._loop = asyncio.get_running_loop()  # Phase 7: for threadsafe emits
        # no waiting for UAC dialogs or subprocess timeouts.
        asyncio.get_event_loop().run_in_executor(None, _ensure_network_access, PORT)

        # Generate the TLS pair on first run so no private key ships in the repo.
        _ensure_certs()

        use_ssl  = self._ssl_enabled()
        ssl_key, ssl_cert = _cert_pair()

        if use_ssl:
            asyncio.create_task(self._serve_alias())

        cfg = uvicorn.Config(
            self.app, host="0.0.0.0", port=PORT, log_level="warning",
            **({"ssl_keyfile": str(ssl_key), "ssl_certfile": str(ssl_cert)} if use_ssl else {}),
        )

        proto = "https" if use_ssl else "http"
        print(f"[Dashboard] {proto}://{self._ip}:{PORT}")
        print("[Dashboard] Press 'Remote Control' in SHIRAZI UI to get the QR code.")
        await uvicorn.Server(cfg).serve()
