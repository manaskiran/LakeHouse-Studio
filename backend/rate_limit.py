"""P0.6 — lightweight in-process rate limiting (default ON).

No new dependency: a sliding-window counter per client IP, kept in memory.
Safe because the service always runs as a single uvicorn worker (see
`run.sh` / the systemd unit) — there is no cross-process state to
reconcile. If that ever changes to multi-worker, this needs to move to a
shared store (Redis) instead.

Two independent limits, both configurable via env vars so an operator can
loosen/disable them without a code change:

* **General flood cap** (`LHS_RATE_LIMIT_MAX` / `LHS_RATE_LIMIT_WINDOW`,
  default 300 requests / 60s per IP): blunts basic request floods without
  interfering with normal UI polling (install-status polling, WS
  reconnects, etc.).
* **Auth-failure lockout** (`LHS_RATE_LIMIT_AUTH_FAIL_MAX` /
  `LHS_RATE_LIMIT_AUTH_FAIL_WINDOW` / `LHS_RATE_LIMIT_AUTH_FAIL_COOLDOWN`,
  default 10 failures / 5min, 5min cooldown): specifically slows down
  brute-forcing `LHS_AUTH_TOKEN` or RBAC credentials. Tracked by watching
  401 responses, independent of what auth mechanism produced them.

`/healthz` is always exempt so uptime monitors are never throttled.
Set `LHS_RATE_LIMIT_ENABLED=0` to disable outright (e.g. for load testing).
"""
from __future__ import annotations

import os
import time
from collections import defaultdict, deque

ENABLED_ENV = "LHS_RATE_LIMIT_ENABLED"
EXEMPT_PATHS = {"/healthz"}


def _truthy(v: str | None) -> bool:
    return (v or "").strip().lower() in ("1", "true", "yes", "on")


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


class SlidingWindowLimiter:
    """Per-key sliding-window request counter. `hit()` records now and
    returns True if the key is still within `limit` over the trailing
    `window` seconds."""

    def __init__(self) -> None:
        self._buckets: dict[str, deque[float]] = defaultdict(deque)

    def hit(self, key: str, limit: int, window: float) -> bool:
        now = time.monotonic()
        bucket = self._buckets[key]
        cutoff = now - window
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True

    def count(self, key: str, window: float) -> int:
        now = time.monotonic()
        bucket = self._buckets.get(key)
        if not bucket:
            return 0
        cutoff = now - window
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        return len(bucket)


class AuthFailureTracker:
    """Records 401 responses per IP; once a caller racks up too many within
    the window, further requests from that IP are refused (429) for a
    cooldown period — regardless of whether later attempts would have
    succeeded. This is what actually slows down credential guessing."""

    def __init__(self) -> None:
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._locked_until: dict[str, float] = {}

    def record_failure(self, key: str, max_failures: int, window: float, cooldown: float) -> None:
        now = time.monotonic()
        bucket = self._failures[key]
        cutoff = now - window
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        bucket.append(now)
        if len(bucket) >= max_failures:
            self._locked_until[key] = now + cooldown

    def is_locked(self, key: str) -> bool:
        until = self._locked_until.get(key)
        if until is None:
            return False
        if time.monotonic() >= until:
            del self._locked_until[key]
            return False
        return True


def client_key(request) -> str:
    """Prefer the leftmost X-Forwarded-For hop (set by a reverse proxy in
    front of Studio, if any) and fall back to the direct peer address."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    client = request.client
    return client.host if client else "unknown"


def client_key_from_scope(scope: dict) -> str:
    """Same lookup as client_key() but from a raw ASGI scope, so the
    middleware doesn't need to construct a Request for every connection
    (websocket scopes don't have one)."""
    headers = {
        k.decode("latin-1").lower(): v.decode("latin-1")
        for k, v in scope.get("headers", []) or ()
    }
    xff = headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    client = scope.get("client")
    return client[0] if client else "unknown"


# Module-level singletons. State must be process-wide (not per-request), and
# tests need a handle to reset it between cases — see tests/test_rate_limit.py.
limiter = SlidingWindowLimiter()
auth_fail_tracker = AuthFailureTracker()


def reset_state() -> None:
    """Test-only: wipe all counters so one test can't bleed into the next."""
    limiter._buckets.clear()
    auth_fail_tracker._failures.clear()
    auth_fail_tracker._locked_until.clear()


def is_enabled() -> bool:
    raw = os.environ.get(ENABLED_ENV)
    if raw is None:
        return True  # default ON
    return _truthy(raw)
