"""dashboard/api_v1.py — versioned SaaS API surface (Phase 8).

`/api/v1/...` is the stable, documented management API. The existing
dashboard routes (/, /login, /auto-login, /api/command, /api/agent, /ws/*)
are the foundation and keep their paths unchanged for compat — see
docs/API.md for the full route map.

Security:
- Every route requires a bearer token (server._auth_token → user_id),
  validated against the general session mechanism (core/accounts).
- Admin routes additionally require the admin role.
- API keys are NEVER accepted or returned here. Key *presence* is
  reported as a bool for status displays; the values stay server-side.
  Keys are managed through the desktop settings UI / environment
  variables, never from the phone.
- Nothing secret is logged; error bodies carry no values.

Registered from DashboardServer._build_app() via register_api_v1(app, server).
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

API_VERSION = "v1"
SAAS_PHASE = "Phase 8 — SaaS foundation"


def _bearer(req: Request) -> str:
    return (req.headers.get("authorization", "")
            .removeprefix("Bearer ").strip())


def _user_id(server, req: Request):
    try:
        return server._auth_token(_bearer(req))
    except Exception:
        return None


def _admin(server, user_id):
    if server._accounts is None or not user_id:
        return None
    try:
        u = server._accounts.get_user(user_id)
        return u if (u and u.is_admin()) else None
    except Exception:
        return None


def _unauth(msg="Unauthorized"):
    return JSONResponse({"error": msg}, status_code=401)


def _forbidden(msg="Admin required"):
    return JSONResponse({"error": msg}, status_code=403)


def _no_accounts():
    return JSONResponse({"error": "accounts layer unavailable"},
                        status_code=503)


def register_api_v1(app, server) -> None:
    """Attach all /api/v1 routes to the dashboard FastAPI app."""

    # ── health / identity ──────────────────────────────────────────────
    @app.get("/api/v1/health")
    async def v1_health():
        return JSONResponse({
            "ok": True, "api": API_VERSION, "saas": SAAS_PHASE,
            "accounts": server._accounts is not None,
            "sessions": server._sessions is not None,
        })

    @app.get("/api/v1/me")
    async def v1_me(req: Request):
        uid = _user_id(server, req)
        if not uid:
            return _unauth()
        if server._accounts is None:
            return _no_accounts()
        u = server._accounts.get_user(uid)
        if not u:
            return _unauth("unknown user")
        return JSONResponse({
            "id": u.id, "name": u.name, "role": u.role,
            "voice": u.voice, "language": u.language, "avatar": u.avatar,
            "paid_opt_in": u.paid_opt_in,
            "prefs": server._accounts.list_prefs(uid),
        })

    # ── users (admin) ──────────────────────────────────────────────────
    @app.get("/api/v1/users")
    async def v1_users(req: Request):
        uid = _user_id(server, req)
        if not uid:
            return _unauth()
        if _admin(server, uid) is None:
            return _forbidden()
        users = [{
            "id": u.id, "name": u.name, "role": u.role,
            "voice": u.voice, "language": u.language,
            "paid_opt_in": u.paid_opt_in, "last_seen": u.last_seen,
        } for u in server._accounts.list_users()]
        return JSONResponse({"users": users})

    @app.post("/api/v1/users")
    async def v1_create_user(req: Request):
        uid = _user_id(server, req)
        if not uid:
            return _unauth()
        if _admin(server, uid) is None:
            return _forbidden()
        try:
            body = await req.json()
        except Exception:
            return JSONResponse({"error": "bad JSON"}, status_code=400)
        new_id = str(body.get("id") or "").strip()
        role = str(body.get("role") or "standard").strip()
        if not new_id or role not in ("admin", "standard"):
            return JSONResponse(
                {"error": "need {id, role: admin|standard}"}, status_code=400)
        if server._accounts.get_user(new_id):
            return JSONResponse({"error": "user exists"}, status_code=409)
        u = server._accounts.ensure_user(
            new_id, name=str(body.get("name") or ""), role=role,
            voice=str(body.get("voice") or "Charon"),
            language=str(body.get("language") or "en"))
        return JSONResponse({"ok": True, "id": u.id, "role": u.role})

    # ── sessions ───────────────────────────────────────────────────────
    @app.get("/api/v1/sessions")
    async def v1_sessions(req: Request):
        uid = _user_id(server, req)
        if not uid:
            return _unauth()
        if server._sessions is None:
            return _no_accounts()
        return JSONResponse(
            {"sessions": server._sessions.list_sessions(uid)})

    @app.post("/api/v1/sessions/revoke-all")
    async def v1_revoke_all_sessions(req: Request):
        uid = _user_id(server, req)
        if not uid:
            return _unauth()
        if server._sessions is None:
            return _no_accounts()
        n = server._sessions.revoke_all(uid, except_token=_bearer(req))
        return JSONResponse({"ok": True, "revoked": n})

    @app.post("/api/v1/logout")
    async def v1_logout(req: Request):
        tok = _bearer(req)
        uid = _user_id(server, req)
        if not uid:
            return _unauth()
        if server._sessions is not None:
            server._sessions.revoke(tok)
        server._tokens.discard(tok)  # legacy in-memory set
        return JSONResponse({"ok": True})

    # ── devices ────────────────────────────────────────────────────────
    @app.get("/api/v1/devices")
    async def v1_devices(req: Request):
        uid = _user_id(server, req)
        if not uid:
            return _unauth()
        if server._sessions is None:
            return _no_accounts()
        from core.accounts import devices as _devices
        return JSONResponse(
            {"devices": _devices.list_devices(server._sessions, uid)})

    @app.post("/api/v1/devices")
    async def v1_register_device(req: Request):
        uid = _user_id(server, req)
        if not uid:
            return _unauth()
        if server._sessions is None:
            return _no_accounts()
        try:
            body = await req.json()
        except Exception:
            body = {}
        kind = str(body.get("kind") or "phone")
        name = str(body.get("name") or "")
        from core.accounts import devices as _devices
        # The raw device token is returned ONCE — it is stored hashed.
        rec = _devices.register(server._sessions, uid, name=name, kind=kind)
        return JSONResponse({"ok": True, **rec})

    @app.delete("/api/v1/devices/{device_id}")
    async def v1_revoke_device(device_id: str, req: Request):
        uid = _user_id(server, req)
        if not uid:
            return _unauth()
        if server._sessions is None:
            return _no_accounts()
        # list_devices (manager-level) carries the server-side channel key;
        # the public facade strips it — never let it reach a response.
        recs = server._sessions.list_devices(uid, include_revoked=True)
        mine = {d.id for d in recs}
        if device_id not in mine and _admin(server, uid) is None:
            return JSONResponse({"error": "unknown device"}, status_code=404)
        # Phase 9: revoke must cascade to in-memory bearers minted from this
        # device via /api/device-login (they bypass the session records).
        doomed_keys = [d.session_key for d in recs if d.id == device_id]
        ok = server._sessions.revoke_device(device_id)
        if hasattr(server, "_purge_device_bearers"):
            server._purge_device_bearers(doomed_keys)
        return JSONResponse({"ok": bool(ok)})

    # ── usage ──────────────────────────────────────────────────────────
    @app.get("/api/v1/usage")
    async def v1_usage(req: Request):
        uid = _user_id(server, req)
        if not uid:
            return _unauth()
        if server._usage is None:
            return JSONResponse({
                "user_id": uid, "days": 0, "providers": {},
                "notes": ["usage tracking unavailable (accounts layer "
                          "failed to initialise)"],
            })
        try:
            days = int(req.query_params.get("days", "30"))
        except Exception:
            days = 30
        days = max(1, min(days, 365))
        return JSONResponse(server._usage.report(uid, days=days))

    # ── providers (admin for writes; status readable by the user) ──────
    @app.get("/api/v1/providers")
    async def v1_providers(req: Request):
        uid = _user_id(server, req)
        if not uid:
            return _unauth()
        if server._accounts is None:
            return _no_accounts()
        import core.accounts.providers as _prov
        chain = _prov.effective_chain(server._accounts, uid)
        return JSONResponse({
            "chain": chain,
            "providers": [{
                "name": s.name, "tier": s.tier, "enabled": s.enabled,
                "available": s.available, "reason": s.reason,
                "key_configured": s.key_configured,  # bool only, never values
                "capabilities": s.capabilities,
            } for s in _prov.provider_status(server._accounts, uid)],
            "free_first": "Free tiers/local are the defaults; paid "
                          "providers need explicit opt-in and are never "
                          "assumed.",
        })

    @app.post("/api/v1/providers/config")
    async def v1_provider_config(req: Request):
        uid = _user_id(server, req)
        if not uid:
            return _unauth()
        if _admin(server, uid) is None:
            return _forbidden()
        try:
            body = await req.json()
        except Exception:
            return JSONResponse({"error": "bad JSON"}, status_code=400)
        # Defense in depth: keys are NEVER accepted here, even though the
        # store would refuse them too.
        for banned in ("api_key", "key", "secret", "token"):
            if banned in body:
                return JSONResponse(
                    {"error": f"'{banned}' not accepted: keys are managed "
                              "via the desktop settings / env vars, never "
                              "the phone API"}, status_code=400)
        import core.accounts.providers as _prov
        name = str(body.get("provider") or "")
        try:
            if "enabled" in body:
                _prov.set_enabled(server._accounts, uid, name,
                                 bool(body["enabled"]), actor_id=uid)
            if body.get("primary") is True:
                chain = _prov.set_primary(server._accounts, uid, name,
                                          actor_id=uid)
            elif isinstance(body.get("chain"), list):
                chain = _prov.set_chain(server._accounts, uid,
                                        [str(c) for c in body["chain"]],
                                        actor_id=uid)
            else:
                chain = _prov.effective_chain(server._accounts, uid)
        except (ValueError, PermissionError) as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse({"ok": True, "provider": name, "chain": chain})

    @app.post("/api/v1/providers/paid-opt-in")
    async def v1_paid_opt_in(req: Request):
        uid = _user_id(server, req)
        if not uid:
            return _unauth()
        if _admin(server, uid) is None:
            return _forbidden()
        try:
            body = await req.json()
        except Exception:
            body = {}
        import core.accounts.providers as _prov
        try:
            on = _prov.opt_in_paid(server._accounts, uid, actor_id=uid,
                                   opt_in=bool(body.get("opt_in", True)))
        except (ValueError, PermissionError) as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse({"ok": True, "paid_opt_in": on,
                             "note": "explicit opt-in recorded; free tiers "
                                     "remain the defaults"})

    # ── permissions ────────────────────────────────────────────────────
    @app.get("/api/v1/permissions")
    async def v1_permissions(req: Request):
        uid = _user_id(server, req)
        if not uid:
            return _unauth()
        if server._accounts is None:
            return _no_accounts()
        from core.accounts import user_permissions as _perm
        return JSONResponse({
            "user_id": uid,
            "role": (server._accounts.get_user(uid).role
                     if server._accounts.get_user(uid) else "?"),
            "effective": _perm.effective_policy(server._accounts, uid),
            "overrides": _perm.list_overrides(server._accounts, uid),
        })
