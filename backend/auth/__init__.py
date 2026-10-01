"""Roles and the acting user.

Login is a dummy for now: the browser sends the chosen role and the name the person typed with
every request (headers X-SWM-Role and X-SWM-User). Nothing is verified, so these values record
who said they did something, not who provably did it. Real authentication can later replace
`actor()` without changing the callers.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote

ROLES_FILE = Path(__file__).resolve().parent / "roles.json"


@lru_cache(maxsize=1)
def registry() -> dict:
    return json.loads(ROLES_FILE.read_text(encoding="utf-8"))


def roles() -> list[dict]:
    return registry()["roles"]


def role(key: str) -> dict:
    for r in roles():
        if r["key"] == key:
            return r
    raise KeyError(f"Unknown role '{key}'")


def actor(headers) -> dict:
    """The acting user from request headers: {"name", "role", "verified": False}.

    Raises KeyError for a role that is not in the registry."""
    role_key = (headers.get("x-swm-role") or "").strip()
    name = unquote((headers.get("x-swm-user") or "").strip())
    if role_key:
        role(role_key)
    return {"name": name or None, "role": role_key or None, "verified": False}
