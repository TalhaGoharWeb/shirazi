"""Phase-2 test: dashboard PIN rate limiting.

- 5 failed PIN attempts -> 6th returns 429
- 429 blocks further attempts during lockout
- a successful login clears the counter (attempts after success start fresh)
- /auto-login failure path also counts and locks out
"""
import sys

sys.path.insert(0, "/home/hatch/workspace/shirazi")

from fastapi.testclient import TestClient

import dashboard.server as srv


def fresh_server():
    server = srv.DashboardServer()
    return server, TestClient(server.app)


def test_login_rate_limit():
    server, client = fresh_server()
    statuses = []
    for _ in range(srv.PIN_MAX_ATTEMPTS):
        r = client.post("/login", json={"pin": "WRONG1"})
        statuses.append(r.status_code)
    assert all(s == 401 for s in statuses), f"expected 5x401, got {statuses}"
    r = client.post("/login", json={"pin": "WRONG1"})
    assert r.status_code == 429, f"expected 429 after {srv.PIN_MAX_ATTEMPTS} fails, got {r.status_code}"
    assert "Too many attempts" in r.json()["error"], r.json()
    # still locked
    r = client.post("/login", json={"pin": "WRONG1"})
    assert r.status_code == 429
    print("PASS: /login locks out after", srv.PIN_MAX_ATTEMPTS, "fails (429)")


def test_success_resets_counter():
    server, client = fresh_server()
    for _ in range(srv.PIN_MAX_ATTEMPTS - 1):
        client.post("/login", json={"pin": "WRONG1"})  # 4 fails -> not locked
    key = server.new_key()
    r = client.post("/login", json={"pin": key.lower()})
    assert r.status_code == 200 and r.json()["ok"], r.text
    # counter cleared: 5 more fails should be 401s again, not 429
    statuses = [client.post("/login", json={"pin": "WRONG1"}).status_code
                for _ in range(srv.PIN_MAX_ATTEMPTS)]
    assert all(s == 401 for s in statuses), f"counter not reset: {statuses}"
    print("PASS: successful login resets the failure counter")


def test_lockout_expires():
    server, client = fresh_server()
    for _ in range(srv.PIN_MAX_ATTEMPTS):
        client.post("/login", json={"pin": "WRONG1"})
    r = client.post("/login", json={"pin": "WRONG1"})
    assert r.status_code == 429
    # simulate lockout expiry
    ip = next(iter(server._pin_attempts))
    server._pin_attempts[ip][2] = 0.0
    r = client.post("/login", json={"pin": "WRONG1"})
    assert r.status_code == 401, f"expected 401 after expiry, got {r.status_code}"
    print("PASS: lockout expires and attempts resume")


def test_auto_login_rate_limit():
    server, client = fresh_server()
    for _ in range(srv.PIN_MAX_ATTEMPTS):
        r = client.get("/auto-login", params={"key": "BADKEY"})
        assert r.status_code == 200, r.status_code  # "Link Expired" page
    r = client.get("/auto-login", params={"key": "BADKEY"})
    assert r.status_code == 429, f"expected 429, got {r.status_code}"
    # a valid QR key still works from a fresh (non-locked) IP? locked IP gets 429 even with right key
    key = server.new_key()
    r = client.get("/auto-login", params={"key": key})
    assert r.status_code == 429, f"locked IP should be blocked even with valid key, got {r.status_code}"
    print("PASS: /auto-login failure path is rate-limited")


if __name__ == "__main__":
    test_login_rate_limit()
    test_success_resets_counter()
    test_lockout_expires()
    test_auto_login_rate_limit()
    print("ALL PIN RATE-LIMIT TESTS PASSED")
