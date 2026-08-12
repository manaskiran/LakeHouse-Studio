"""P0.5 — WebSocket log endpoints must honor the same auth as HTTP routes.

Before this fix, `/api/installs/{id}/logs` and
`/api/installs/{id}/services/{name}/logs/stream` were guarded only by an
Origin-header check, even when `LHS_AUTH_TOKEN` (or RBAC) was configured.
That meant anyone who could reach the port — sending a same-host/absent
Origin header — could stream live install logs without ever presenting a
token, bypassing the auth that every HTTP route already enforces.

Since browsers cannot set an Authorization header on a WebSocket handshake,
the fix accepts the token via `?token=` (mirroring the existing `?last_seq=`
query param the frontend already sends) in addition to an Authorization
header for non-browser clients.

Hermetic: no live HTTP, no Docker, no RBAC DB (legacy single-token mode
only — RBAC's own WS coverage lives in test_rbac.py).
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDisconnect

import backend.main as main_mod
from backend.events import bus
from backend.models import LogEvent


@pytest.fixture()
def client() -> TestClient:
    return TestClient(main_mod.app)


@pytest.fixture(autouse=True)
def _reset_auth_token(monkeypatch):
    """AUTH_TOKEN is read once at import time, so tests must patch the
    module attribute directly rather than the env var."""
    monkeypatch.setattr(main_mod, "AUTH_TOKEN", None, raising=False)
    yield
    monkeypatch.setattr(main_mod, "AUTH_TOKEN", None, raising=False)


def _seed_event(install_id: str) -> None:
    bus.publish_nowait(LogEvent(
        install_id=install_id, ts=time.time(), kind="log",
        stream="stdout", line="hello",
    ))


def _cleanup(install_id: str) -> None:
    bus._history.pop(install_id, None)  # noqa: SLF001 — test-only cleanup
    bus._next_seq.pop(install_id, None)  # noqa: SLF001


def test_logs_ws_open_when_auth_disabled(client: TestClient) -> None:
    """No LHS_AUTH_TOKEN configured == today's dev/local behavior: connects
    with no token needed. Guards against a regression that would break
    every local install."""
    install_id = "ws-auth-off"
    _seed_event(install_id)
    try:
        with client.websocket_connect(f"/api/installs/{install_id}/logs") as ws:
            payload = ws.receive_json()
        assert payload["line"] == "hello"
    finally:
        _cleanup(install_id)


def test_logs_ws_rejects_missing_token_when_auth_enabled(
    client: TestClient, monkeypatch
) -> None:
    """This is the vulnerability: with AUTH_TOKEN set, connecting with no
    token at all must now be refused instead of silently streaming logs."""
    monkeypatch.setattr(main_mod, "AUTH_TOKEN", "s3cr3t-token", raising=False)
    install_id = "ws-auth-on-no-token"
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/api/installs/{install_id}/logs"):
            pass


def test_logs_ws_rejects_wrong_token_when_auth_enabled(
    client: TestClient, monkeypatch
) -> None:
    monkeypatch.setattr(main_mod, "AUTH_TOKEN", "s3cr3t-token", raising=False)
    install_id = "ws-auth-on-wrong-token"
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            f"/api/installs/{install_id}/logs?token=wrong"
        ):
            pass


def test_logs_ws_accepts_correct_query_token_when_auth_enabled(
    client: TestClient, monkeypatch
) -> None:
    """Browsers can't set Authorization on a WS handshake, so the query
    param is the real-world path the frontend uses — must work."""
    monkeypatch.setattr(main_mod, "AUTH_TOKEN", "s3cr3t-token", raising=False)
    install_id = "ws-auth-on-good-token"
    _seed_event(install_id)
    try:
        with client.websocket_connect(
            f"/api/installs/{install_id}/logs?token=s3cr3t-token"
        ) as ws:
            payload = ws.receive_json()
        assert payload["line"] == "hello"
    finally:
        _cleanup(install_id)


def test_logs_ws_accepts_bearer_header_when_auth_enabled(
    client: TestClient, monkeypatch
) -> None:
    """Non-browser clients (curl, test tools) may still use a header."""
    monkeypatch.setattr(main_mod, "AUTH_TOKEN", "s3cr3t-token", raising=False)
    install_id = "ws-auth-on-header-token"
    _seed_event(install_id)
    try:
        with client.websocket_connect(
            f"/api/installs/{install_id}/logs",
            headers={"authorization": "Bearer s3cr3t-token"},
        ) as ws:
            payload = ws.receive_json()
        assert payload["line"] == "hello"
    finally:
        _cleanup(install_id)


class _FakeWebSocket:
    """Minimal stand-in exposing just what _ws_auth_ok reads. The real
    ws_service_logs endpoint checks store/manifest state *before* auth, so
    exercising it end-to-end would require faking a full install record;
    the shared helper is what both WS routes actually delegate auth to, so
    unit-testing it directly is the precise way to cover
    ws_service_logs — it previously had NO token check at all in legacy
    (non-RBAC) mode, same gap as ws_logs."""

    def __init__(self, headers=None, query_params=None):
        self.headers = headers or {}
        self.query_params = query_params or {}


@pytest.mark.asyncio
async def test_ws_auth_ok_rejects_missing_token_when_auth_enabled(monkeypatch) -> None:
    monkeypatch.setattr(main_mod, "AUTH_TOKEN", "s3cr3t-token", raising=False)
    ok = await main_mod._ws_auth_ok(_FakeWebSocket(), "/api/installs/{install_id}/logs")
    assert ok is False


@pytest.mark.asyncio
async def test_ws_auth_ok_accepts_query_token_when_auth_enabled(monkeypatch) -> None:
    monkeypatch.setattr(main_mod, "AUTH_TOKEN", "s3cr3t-token", raising=False)
    ok = await main_mod._ws_auth_ok(
        _FakeWebSocket(query_params={"token": "s3cr3t-token"}),
        "/api/installs/{install_id}/logs",
    )
    assert ok is True


@pytest.mark.asyncio
async def test_ws_auth_ok_rejects_wrong_query_token_when_auth_enabled(monkeypatch) -> None:
    monkeypatch.setattr(main_mod, "AUTH_TOKEN", "s3cr3t-token", raising=False)
    ok = await main_mod._ws_auth_ok(
        _FakeWebSocket(query_params={"token": "wrong"}),
        "/api/installs/{install_id}/logs",
    )
    assert ok is False


@pytest.mark.asyncio
async def test_ws_auth_ok_allows_all_when_auth_disabled(monkeypatch) -> None:
    monkeypatch.setattr(main_mod, "AUTH_TOKEN", None, raising=False)
    ok = await main_mod._ws_auth_ok(_FakeWebSocket(), "/api/installs/{install_id}/logs")
    assert ok is True
