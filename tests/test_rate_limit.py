"""P0.6 — rate limiting (default ON, opt-out via LHS_RATE_LIMIT_ENABLED=0).

Two independent limits live in backend/rate_limit.py and are wired in as
ASGI middleware in backend/main.py:

  * general flood cap — N requests / window seconds per client IP
  * auth-failure lockout — after N 401 responses, further requests from
    that IP get 429'd for a cooldown, regardless of whether they'd have
    succeeded (this is what actually slows down token brute-forcing)

The whole-suite default (see conftest.py) is LHS_RATE_LIMIT_ENABLED=0, so
these tests explicitly re-enable it and reset the module-level counters
between cases — the limiter state is process-wide by design (single
uvicorn worker), which means it's also shared across tests unless we clear
it ourselves.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import backend.main as main_mod
from backend import rate_limit


@pytest.fixture()
def client() -> TestClient:
    return TestClient(main_mod.app)


@pytest.fixture(autouse=True)
def _enabled_and_clean(monkeypatch):
    monkeypatch.setenv("LHS_RATE_LIMIT_ENABLED", "1")
    rate_limit.reset_state()
    yield
    rate_limit.reset_state()


def test_general_cap_allows_requests_under_the_limit(client, monkeypatch):
    monkeypatch.setenv("LHS_RATE_LIMIT_MAX", "5")
    monkeypatch.setenv("LHS_RATE_LIMIT_WINDOW", "60")
    for _ in range(5):
        r = client.get("/api/goals")
        assert r.status_code != 429


def test_general_cap_rejects_once_exceeded(client, monkeypatch):
    monkeypatch.setenv("LHS_RATE_LIMIT_MAX", "5")
    monkeypatch.setenv("LHS_RATE_LIMIT_WINDOW", "60")
    for _ in range(5):
        client.get("/api/goals")
    r = client.get("/api/goals")
    assert r.status_code == 429
    assert "rate limited" in r.json()["error"]


def test_healthz_is_never_rate_limited(client, monkeypatch):
    monkeypatch.setenv("LHS_RATE_LIMIT_MAX", "2")
    monkeypatch.setenv("LHS_RATE_LIMIT_WINDOW", "60")
    for _ in range(10):
        r = client.get("/healthz")
        assert r.status_code == 200


def test_disabled_via_env_allows_unlimited_requests(client, monkeypatch):
    monkeypatch.setenv("LHS_RATE_LIMIT_ENABLED", "0")
    monkeypatch.setenv("LHS_RATE_LIMIT_MAX", "1")
    monkeypatch.setenv("LHS_RATE_LIMIT_WINDOW", "60")
    for _ in range(10):
        r = client.get("/api/goals")
        assert r.status_code != 429


def test_auth_failure_lockout_trips_after_repeated_401s(client, monkeypatch):
    monkeypatch.setattr(main_mod, "AUTH_TOKEN", "real-token", raising=False)
    monkeypatch.setenv("LHS_RATE_LIMIT_MAX", "1000")  # isolate from the general cap
    monkeypatch.setenv("LHS_RATE_LIMIT_WINDOW", "60")
    monkeypatch.setenv("LHS_RATE_LIMIT_AUTH_FAIL_MAX", "3")
    monkeypatch.setenv("LHS_RATE_LIMIT_AUTH_FAIL_WINDOW", "60")
    monkeypatch.setenv("LHS_RATE_LIMIT_AUTH_FAIL_COOLDOWN", "60")

    # 3 wrong-token attempts trip the lockout.
    for _ in range(3):
        r = client.get(
            "/api/goals", headers={"Authorization": "Bearer wrong-token"}
        )
        assert r.status_code == 401

    # A 4th attempt — even with the CORRECT token — must now be locked out.
    r = client.get(
        "/api/goals", headers={"Authorization": "Bearer real-token"}
    )
    assert r.status_code == 429


def test_correct_auth_does_not_count_as_a_failure(client, monkeypatch):
    monkeypatch.setattr(main_mod, "AUTH_TOKEN", "real-token", raising=False)
    monkeypatch.setenv("LHS_RATE_LIMIT_MAX", "1000")
    monkeypatch.setenv("LHS_RATE_LIMIT_WINDOW", "60")
    monkeypatch.setenv("LHS_RATE_LIMIT_AUTH_FAIL_MAX", "3")
    monkeypatch.setenv("LHS_RATE_LIMIT_AUTH_FAIL_WINDOW", "60")
    for _ in range(10):
        r = client.get(
            "/api/goals", headers={"Authorization": "Bearer real-token"}
        )
        assert r.status_code == 200
