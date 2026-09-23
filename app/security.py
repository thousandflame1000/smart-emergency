"""Role checks for endpoints that must not be open to everyone once auth is turned on.

Mirrors the rest of the app's opt-in philosophy (see app/demo_auth.py): when no
DEMO_PASSWORD is set and no LINE-bound admin exists yet ("open" mode), nothing here
blocks anything — that keeps local dev and a fresh, not-yet-configured deployment
usable. Once auth is actually enforced, these become real per-role gates: `require_admin`
is unchanged in spirit from before field_staff existed; `require_staff` is the new,
broader gate for data-entry endpoints (editing resources and resource points) that
field_staff should reach but the rest of the admin console should not need.
"""
from fastapi import HTTPException

from app.demo_auth import auth_mode
from app.services.admin_session import current_admin


def require_admin() -> dict | None:
    principal = current_admin.get()
    if principal is None and auth_mode() == "open":
        return None
    if not principal or "admin" not in (principal.get("roles") or []):
        raise HTTPException(status_code=403, detail="Administrator authentication required")
    return principal


def require_staff() -> dict | None:
    """Admin or field_staff — the two roles allowed to create/edit resources and resource points."""
    principal = current_admin.get()
    if principal is None and auth_mode() == "open":
        return None
    roles = set((principal or {}).get("roles") or [])
    if not roles & {"admin", "field_staff"}:
        raise HTTPException(status_code=403, detail="Staff or administrator authentication required")
    return principal
