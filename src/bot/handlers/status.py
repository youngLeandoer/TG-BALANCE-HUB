from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command
import asyncio
from datetime import datetime
import json
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from src.database.models import User, Service
from src.database.session import async_session_maker
from src.core.logger import setup_logger
from src.services.providers.umnico import UmnicoConnector
from src.services.providers.hosterby import HosterByConnector
from src.services.providers.smsaero import SMSAeroConnector
from src.services.providers.avito import AvitoConnector
from src.services.providers.regru import RegRuConnector

logger = setup_logger(__name__)
router = Router()

SERVICE_CONNECTORS = {
    "umnico": UmnicoConnector,
    "avito": AvitoConnector,
    "regru": RegRuConnector,
    "hosterby": HosterByConnector,
    "smsaero": SMSAeroConnector,
}


@router.message(Command("status"))
async def cmd_status(message: types.Message):
    tg_id = message.from_user.id
    logger.info(f"/status requested by tg_id={tg_id}")
    
    async with async_session_maker() as session:
        result = await session.execute(
            select(User)
            .where(User.tg_id == tg_id)
            .options(selectinload(User.services))
        )
        user = result.scalar_one_or_none()
        
        if not user or not user.services:
            await message.answer(
                "📊 <b>Статус сервисов</b>\n\n"
                "У вас пока нет добавленных сервисов.\n"
                "Используйте /add для добавления"
            )
            return
        
        report = "📊 <b>Статус сервисов</b>\n\n"
        
        for service in user.services:
            if not service.is_active:
                logger.info(f"Skip inactive service id={service.id} name={service.service_name}")
                continue

            # Temporarily disabled due to regional access restrictions.
            if service.service_name == "mango_scraper":
                logger.info(f"Skip temporarily disabled service id={service.id} name={service.service_name}")
                continue
            
            try:
                credentials = service.credentials
                if isinstance(credentials, str):
                    credentials = json.loads(credentials)

            except Exception as parse_error:
                logger.error(f"Credentials parse failed: {parse_error}")
                report += f"❌ <b>{service.service_name.title()}</b>\n"
                report += f"Ошибка чтения credentials: {str(parse_error)}\n\n"
                continue
            
            connector_cls = SERVICE_CONNECTORS.get(service.service_name)
            if not connector_cls:
                logger.warning(f"No connector for service id={service.id} name={service.service_name}")
                report += f"❌ <b>{service.service_name.title()}</b>\n"
                report += "Ошибка: сервис пока не поддерживается\n\n"
                continue

            # Local browser helper can push Mango balance snapshot via internal endpoint.
            if service.service_name == "mango_scraper" and "manual_balance" in credentials:
                service_title = service.service_name.title()
                raw_label = credentials.get("label")
                if raw_label:
                    service_title = f"{service_title} ({raw_label})"
                report += f"✅ <b>{service_title}</b>\n"
                report += f"Баланс: {credentials.get('manual_balance')} {credentials.get('manual_currency', 'RUB')}\n\n"
                service.last_check = datetime.utcnow()
                continue

            logger.info(
                f"Checking service id={service.id} name={service.service_name} with connector={connector_cls.__name__}"
            )
            connector = connector_cls(credentials=credentials)
            try:
                # Один проблемный провайдер не должен подвешивать весь /status.
                balance_data = await asyncio.wait_for(connector.get_balance_data(), timeout=12.0)
            except asyncio.TimeoutError:
                logger.warning(f"Service check timeout id={service.id} name={service.service_name}")
                report += f"❌ <b>{service.service_name.title()}</b>\n"
                report += "Ошибка: таймаут запроса к API сервиса(проблема со стороны сервиса)\n\n"
                continue
            
            if balance_data.status == "OK":
                service_title = service.service_name.title()
                # Optional friendly account label for multi-account providers.
                raw_label = credentials.get("label")
                if not raw_label and service.service_name == "mango_scraper":
                    raw_api_key = credentials.get("api_key", "")
                    if isinstance(raw_api_key, str) and "|" in raw_api_key:
                        # Backward compatibility for plaintext records.
                        raw_label = raw_api_key.split("|", 1)[0].strip()
                if raw_label:
                    service_title = f"{service_title} ({raw_label})"

                report += f"✅ <b>{service_title}</b>\n"
                report += f"Баланс: {balance_data.balance} {balance_data.currency}\n"
                if balance_data.expiration:
                    expiration_dt = balance_data.expiration
                    if expiration_dt.tzinfo is not None:
                        expiration_dt = expiration_dt.astimezone().replace(tzinfo=None)
                    report += f"Оплатить до: {expiration_dt.strftime('%d.%m.%Y %H:%M')}\n"
            else:
                service_title = service.service_name.title()
                raw_label = credentials.get("label")
                if raw_label:
                    service_title = f"{service_title} ({raw_label})"
                report += f"❌ <b>{service_title}</b>\n"
                report += f"Ошибка: {balance_data.error_message}\n"
            
            report += "\n"
            service.last_check = datetime.utcnow()
        
        await session.commit()
        await message.answer(report)