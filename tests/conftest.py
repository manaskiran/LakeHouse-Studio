"""Pytest config for Lakehouse Studio tests.

Ensures the project root is on sys.path so `import backend.compatibility`
works regardless of where pytest is invoked from.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


@pytest.fixture(autouse=True)
def _rate_limiting_off_by_default(monkeypatch):
    """P0.6's rate limiter (backend/rate_limit.py) is a process-wide
    singleton and defaults ON. The suite fires hundreds of requests through
    TestClient instances that all share the same fake client address, so
    leaving it enabled here would trip the general flood cap partway
    through the run and fail unrelated tests. Default it off for every
    test; tests/test_rate_limit.py explicitly re-enables + resets it."""
    monkeypatch.setenv("LHS_RATE_LIMIT_ENABLED", "0")
