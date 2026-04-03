import asyncio
import logging
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, StateFilter
from aiogram.fsm.storage.redis import RedisStorage
from redis.asyncio import from_url
from src.core.config import settings
from src.core.report_formatting import SERVICE_MESSAGE_DELAY_SEC
from src.core.logger import setup_logger
from src.database.init_db import init_db

from src.bot.handlers.add_service import router as add_service_router
from src.bot.handlers.chat_id import router as chat_id_router
from src.bot.handlers.daily_report import router as daily_report_router
from src.bot.handlers.healthcheck import router as healthcheck_router
from src.bot.handlers.status import router as status_router
from src.bot.handlers.stats import router as stats_router
from src.bot.handlers.remove_service import router as remove_service_router
from src.bot.keyboards.main_menu import get_main_menu_keyboard
from src.services.daily_report import build_daily_group_report

logger = setup_logger(__name__)
daily_report_task: asyncio.Task | None = None

MAIN_MENU_BUTTONS = (
    "➕ Добавить сервис",
    "📊 Статус сервисов",
    "🗑 Удалить сервис",
    "📉 Статистика (3 дня)",
    "📅 Статистика (месяц)",
    "🗂 Статистика (всё время)",
    "📤 Экспорт CSV (3 дня)",
    "📤 Экспорт CSV (месяц)",
    "📤 Экспорт CSV (всё время)",
    "ℹ️ Help",
    "🩺 Healthcheck",
    "❌ Отмена",
)


async def on_startup(bot: Bot):
    logger.info(f"Bot started! Bot ID: {bot.id}")
    global daily_report_task
    if settings.DAILY_STATS_ENABLED and settings.DAILY_STATS_CHAT_ID:
        daily_report_task = asyncio.create_task(_daily_report_loop(bot))
        if settings.DAILY_STATS_INTERVAL_MINUTES > 0:
            logger.info(
                "Daily report loop enabled chat_id=%s every %s minutes",
                settings.DAILY_STATS_CHAT_ID,
                settings.DAILY_STATS_INTERVAL_MINUTES,
            )
        else:
            logger.info(
                "Daily report loop enabled chat_id=%s at %02d:%02d UTC",
                settings.DAILY_STATS_CHAT_ID,
                settings.DAILY_STATS_HOUR_UTC,
                settings.DAILY_STATS_MINUTE_UTC,
            )


async def on_shutdown(bot: Bot):
    logger.info("Bot stopped!")
    global daily_report_task
    if daily_report_task:
        daily_report_task.cancel()
        try:
            await daily_report_task
        except asyncio.CancelledError:
            pass
        daily_report_task = None
    await bot.session.close()


def _seconds_until_next_run_utc(hour: int, minute: int) -> float:
    now = datetime.utcnow()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now >= target:
        target = target + timedelta(days=1)
    return max((target - now).total_seconds(), 1.0)


async def _daily_report_loop(bot: Bot):
    while True:
        if settings.DAILY_STATS_INTERVAL_MINUTES > 0:
            delay = max(float(settings.DAILY_STATS_INTERVAL_MINUTES) * 60.0, 10.0)
        else:
            delay = _seconds_until_next_run_utc(
                settings.DAILY_STATS_HOUR_UTC,
                settings.DAILY_STATS_MINUTE_UTC,
            )
        await asyncio.sleep(delay)
        try:
            parts = await build_daily_group_report()
            for i, part in enumerate(parts):
                await bot.send_message(settings.DAILY_STATS_CHAT_ID, part)
                if i + 1 < len(parts):
                    await asyncio.sleep(SERVICE_MESSAGE_DELAY_SEC)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Daily report send failed: %s", exc)
            await asyncio.sleep(60)


async def main():
    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    
    # ✅ Redis для FSM storage
    redis = from_url(settings.REDIS_URL)
    storage = RedisStorage(redis=redis)
    
    dp = Dispatcher(storage=storage)  # ✅ Передаём storage
    
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    
    await init_db()
    
    # ✅ Роутеры ДО echo
    dp.include_router(add_service_router)
    dp.include_router(chat_id_router)
    dp.include_router(daily_report_router)
    dp.include_router(healthcheck_router)
    dp.include_router(status_router)
    dp.include_router(stats_router)
    dp.include_router(remove_service_router)
    
    @dp.message(Command("start"))
    async def start_cmd(message: types.Message):
        await message.answer(
            "🚀 <b>Balance Hub</b> запущен!\n\n"
            "Доступные команды:\n"
            "/add — добавить сервис\n"
            "/remove — удалить сервис\n"
            "/status — проверить балансы\n"
            "/stats — статистика (по умолчанию 3 дня)\n"
            "/stats_export — экспорт статистики в CSV\n"
            "/dailyreport — ручная суточная сводка\n"
            "/chatid — показать ID текущего чата\n"
            "/healthcheck — проверить DB/Redis\n"
            "/help — помощь\n"
            "/cancel — отменить операцию",
            reply_markup=get_main_menu_keyboard(),
        )
    
    @dp.message(Command("help"))
    @dp.message(F.text == "ℹ️ Help")
    async def help_cmd(message: types.Message):
        await message.answer(
            "📚 <b>Справка</b>\n\n"
            "Этот бот помогает отслеживать баланс и сроки оплаты сервисов.\n\n"
            "<b>Команды:</b>\n"
            "/add — добавить новый сервис\n"
            "/remove — удалить сервис из списка\n"
            "/status — проверить баланс всех сервисов\n"
            "/stats — статистика за период (3 дня / месяц / всё время)\n"
            "/stats_export — CSV-экспорт статистики\n"
            "/dailyreport — ручной запуск суточной сводки\n"
            "/chatid — ID текущего чата\n"
            "/healthcheck — статус DB/Redis\n"
            "/cancel — отменить текущую операцию",
            reply_markup=get_main_menu_keyboard(),
        )
    
    @dp.message(Command("cancel"))
    async def cancel_cmd(message: types.Message, state):
        from aiogram.fsm.context import FSMContext
        await state.clear()
        await message.answer("❌ Операция отменена")
    
    # ✅ Echo ТОЛЬКО для не-команд
    @dp.message(
        StateFilter(None),
        F.text & ~F.text.startswith("/") & ~F.text.in_(MAIN_MENU_BUTTONS),
    )
    async def echo(message: types.Message):
        await message.answer(
            "👋 Используйте /add для добавления сервиса\n"
            "Или /help для справки"
        )
    
    logger.info("Starting polling...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot interrupted by user")