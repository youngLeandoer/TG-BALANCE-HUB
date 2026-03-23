from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command
from datetime import datetime
import json
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from src.database.models import User, Service
from src.database.session import async_session_maker
from src.core.logger import setup_logger
from src.services.providers.umnico import UmnicoConnector

logger = setup_logger(__name__)
router = Router()


@router.message(Command("status"))
async def cmd_status(message: types.Message):
    tg_id = message.from_user.id
    
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
            
            connector = UmnicoConnector(credentials=credentials)
            balance_data = await connector.get_balance_data()
            
            if balance_data.status == "OK":
                report += f"✅ <b>{service.service_name.title()}</b>\n"
                report += f"Баланс: {balance_data.balance} {balance_data.currency}\n"
                if balance_data.expiration:
                    expiration_dt = balance_data.expiration
                    if expiration_dt.tzinfo is not None:
                        expiration_dt = expiration_dt.astimezone().replace(tzinfo=None)
                    report += f"Оплатить до: {expiration_dt.strftime('%d.%m.%Y %H:%M')}\n"
            else:
                report += f"❌ <b>{service.service_name.title()}</b>\n"
                report += f"Ошибка: {balance_data.error_message}\n"
            
            report += "\n"
            service.last_check = datetime.utcnow()
        
        await session.commit()
        await message.answer(report)