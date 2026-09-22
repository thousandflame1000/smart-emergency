from fastapi import HTTPException

from app.services.admin_session import current_admin


def require_admin() -> dict:
    principal = current_admin.get()
    if not principal:
        raise HTTPException(status_code=403, detail="Administrator authentication required")
    return principal
