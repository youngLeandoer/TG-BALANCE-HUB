from datetime import datetime
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from src.core.config import settings
from src.database.models import User, Service
from src.database.session import async_session_maker

app = FastAPI(title="Balance Hub OAuth")


@app.get("/")
async def root():
    return {"service": "Balance Hub OAuth", "status": "running"}


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.get("/oauth/callback/{service_name}")
async def oauth_callback(service_name: str, code: str = None, error: str = None):
    """Endpoint для обработки OAuth колбэков"""
    if error:
        return {"error": error}
    # Логика обработки кода будет добавлена позже
    return {"service": service_name, "status": "code_received"}


class MangoBalanceUpdateRequest(BaseModel):
    tg_id: int
    label: str
    balance: float
    currency: str = "RUB"


@app.post("/internal/mango/balance")
async def update_mango_balance(
    payload: MangoBalanceUpdateRequest,
    x_internal_token: str | None = Header(default=None),
):
    if not settings.INTERNAL_UPDATE_TOKEN:
        raise HTTPException(status_code=503, detail="INTERNAL_UPDATE_TOKEN is not configured")
    if x_internal_token != settings.INTERNAL_UPDATE_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid internal token")

    async with async_session_maker() as session:
        user_result = await session.execute(select(User).where(User.tg_id == payload.tg_id))
        user = user_result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        services_result = await session.execute(
            select(Service).where(Service.user_id == user.id, Service.service_name == "mango_scraper")
        )
        services = services_result.scalars().all()

        target_service = None
        for service in services:
            credentials = service.credentials or {}
            if isinstance(credentials, dict) and credentials.get("label") == payload.label:
                target_service = service
                break

        if not target_service:
            raise HTTPException(status_code=404, detail="Mango service with this label not found")

        credentials = dict(target_service.credentials or {})
        credentials["manual_balance"] = payload.balance
        credentials["manual_currency"] = payload.currency
        credentials["manual_updated_at"] = datetime.utcnow().isoformat()
        target_service.credentials = credentials
        target_service.last_check = datetime.utcnow()
        await session.commit()

    return {"status": "ok", "label": payload.label, "balance": payload.balance}
