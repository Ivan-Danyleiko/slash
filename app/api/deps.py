from fastapi import Depends, Header, HTTPException

from app.core.config import get_settings


def require_admin(x_api_key: str = Header(default="")) -> None:
    settings = get_settings()
    if x_api_key != settings.admin_api_key:
        raise HTTPException(status_code=401, detail="Invalid admin API key")
