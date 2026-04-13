from datetime import datetime
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from src.api.router import router as api_v1_router
from src.core.config import settings
from src.core.workspace import get_or_create_workspace_user
from src.database.models import Service, BalanceHistory
from src.database.session import async_session_maker

app = FastAPI(title="Balance Hub OAuth")
app.include_router(api_v1_router)


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


class ManualBalanceUpdateRequest(BaseModel):
    """tg_id в теле опционален: привязка идёт к общему workspace (SHARED_WORKSPACE_TG_ID / BOT_ADMINS)."""
    tg_id: int | None = None
    label: str
    balance: float
    currency: str = "RUB"


async def _apply_manual_balance(
    payload: ManualBalanceUpdateRequest,
    service_name: str,
    x_internal_token: str | None,
) -> dict:
    if not settings.INTERNAL_UPDATE_TOKEN:
        raise HTTPException(status_code=503, detail="INTERNAL_UPDATE_TOKEN is not configured")
    if x_internal_token != settings.INTERNAL_UPDATE_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid internal token")

    async with async_session_maker() as session:
        user = await get_or_create_workspace_user(session)

        services_result = await session.execute(
            select(Service).where(Service.user_id == user.id, Service.service_name == service_name)
        )
        services = services_result.scalars().all()

        target_service = None
        for service in services:
            credentials = service.credentials or {}
            if isinstance(credentials, dict) and credentials.get("label") == payload.label:
                target_service = service
                break

        if not target_service:
            raise HTTPException(
                status_code=404,
                detail=f"Service {service_name} with this label not found",
            )

        credentials = dict(target_service.credentials or {})
        credentials["manual_balance"] = payload.balance
        credentials["manual_currency"] = payload.currency
        credentials["manual_updated_at"] = datetime.utcnow().isoformat()
        target_service.credentials = credentials
        target_service.last_check = datetime.utcnow()
        session.add(
            BalanceHistory(
                service_id=target_service.id,
                balance=float(payload.balance),
                currency=str(payload.currency),
                status="OK",
                checked_at=datetime.utcnow(),
            )
        )
        await session.commit()

    return {"status": "ok", "label": payload.label, "balance": payload.balance, "service": service_name}


@app.post("/internal/adminvps/balance")
async def update_adminvps_balance(
    payload: ManualBalanceUpdateRequest,
    x_internal_token: str | None = Header(default=None),
):
    return await _apply_manual_balance(payload, "adminvps_scraper", x_internal_token)


@app.post("/internal/atlex/balance")
async def update_atlex_balance(
    payload: ManualBalanceUpdateRequest,
    x_internal_token: str | None = Header(default=None),
):
    return await _apply_manual_balance(payload, "atlex_scraper", x_internal_token)


@app.post("/internal/nic/balance")
async def update_nic_balance(
    payload: ManualBalanceUpdateRequest,
    x_internal_token: str | None = Header(default=None),
):
    return await _apply_manual_balance(payload, "nic_scraper", x_internal_token)


@app.post("/internal/mango/balance")
async def update_mango_balance(
    payload: ManualBalanceUpdateRequest,
    x_internal_token: str | None = Header(default=None),
):
    return await _apply_manual_balance(payload, "mango_scraper", x_internal_token)
