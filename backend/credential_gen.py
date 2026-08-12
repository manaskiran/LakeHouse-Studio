"""P0.4b/P0.7 — per-install credential generation (default ON since P0.7).

The stacks ship a documented demo/pilot MinIO secret (``udp_admin_12345``) that
is public in the repo and identical on every install. By default the runner
generates a strong random secret and rewrites the shipped literal across the
install directory so no two installs share the known default. Set
``LHS_GENERATE_CREDENTIALS=0`` (or false/no/off) to opt back out — e.g. to
reproduce the original byte-identical certified demo path for a side-by-side
comparison.

Design constraints that keep this "stable, not compromise":

* **Default ON, explicit opt-out.** Without the flag, a secret is generated.
  Set the flag to a falsy value to fall back to the shipped
  ``${MINIO_ROOT_PASSWORD:-udp_admin_12345}`` default exactly as before.
* **Rotate the SECRET, not the username.** The access key stays ``admin``. The
  password literal is unique, so a plain string replace across install-dir text
  files is unambiguous and complete; the username ``admin`` is a common word and
  is deliberately left alone.
* **Shell/YAML/SQL-safe secret.** ``token_hex`` yields ``[0-9a-f]`` only, so the
  value needs no quoting anywhere it lands (compose YAML, bash, mc args, Trino
  properties, StarRocks SQL).

This module is pure; the runner owns the filesystem sweep.
"""
from __future__ import annotations

import secrets

GENERATE_ENV = "LHS_GENERATE_CREDENTIALS"

# The demo/pilot MinIO secret shipped in the public repo — the single canonical
# literal the install-dir sweep replaces when generation is enabled. Documented,
# non-production, allowlisted in .gitleaks.toml (udp_admin_\d+).
DEMO_MINIO_SECRET = "udp_admin_12345"  # noqa: S105 - documented public placeholder

# Env var name whose value carries the (generated or default) MinIO secret.
MINIO_SECRET_ENV = "MINIO_ROOT_PASSWORD"


def generate_secret() -> str:
    """Return a strong, quoting-safe secret (40 hex chars = 160 bits)."""
    return secrets.token_hex(20)
