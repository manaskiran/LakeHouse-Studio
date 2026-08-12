"""P0.7 — tests for runner._resolve_flag, the default-ON env-flag resolver.

Unlike _is_truthy (opt-in: unset → off), _resolve_flag supports opt-out
flags (unset → the caller's `default`, which may be True). Used first for
LHS_GENERATE_CREDENTIALS (see test_credential_gen.py) but written generic
so future default-on flags reuse it instead of re-deriving the same
precedence rules ad hoc.

Precedence, by design: an explicit disabling value from ANY source beats
an explicit enabling value from another — safer bias when two sources
disagree — and only falls back to `default` when nothing at all was set.
"""
from __future__ import annotations

import pytest

from backend.runner import _resolve_flag


@pytest.mark.parametrize("default", [True, False])
def test_all_unset_falls_back_to_default(default):
    assert _resolve_flag([None, None], default=default) is default


@pytest.mark.parametrize("on_value", ["1", "true", "yes", "on", "anything-else"])
def test_explicit_on_values(on_value):
    assert _resolve_flag([on_value, None], default=False) is True
    assert _resolve_flag([None, on_value], default=False) is True


@pytest.mark.parametrize("off_value", ["0", "false", "no", "off", "disable", "disabled", "FALSE", "Off"])
def test_explicit_off_values_case_insensitive(off_value):
    assert _resolve_flag([off_value, None], default=True) is False
    assert _resolve_flag([None, off_value], default=True) is False


def test_explicit_off_beats_explicit_on_from_another_source():
    assert _resolve_flag(["1", "0"], default=True) is False
    assert _resolve_flag(["off", "true"], default=True) is False


def test_empty_string_treated_as_unset():
    assert _resolve_flag(["", None], default=True) is True
    assert _resolve_flag(["", None], default=False) is False
