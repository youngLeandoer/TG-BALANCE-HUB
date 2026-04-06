from fastapi import Header, HTTPException
from src.core.config import settings


def require_api_token(
    authorization: str | None = Header(default=None),
    x_api_token: str | None = Header(default=None),
) -> None:
    if not settings.API_TOKEN:
        raise HTTPException(status_code=503, detail="API is disabled (API_TOKEN is not set)")
    bearer: str | None = None
    if authorization and authorization.lower().startswith("bearer "):
        bearer = authorization[7:].strip()
    token = bearer or (x_api_token.strip() if x_api_token else None)
    if not token or token != settings.API_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing API token")
