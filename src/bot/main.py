import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from aiogram import Bot, Dispatcher, types, F
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError
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
from src.bot.middlewares.request_guard import RequestGuardMiddleware
from src.bot.request_guard import cancel_current_task, has_running_task
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
    "📡 Healthcheck",
    "🩺 Healthcheck",
    "❌ Отмена",
)


async def on_startup(bot: Bot):
    logger.info(f"Bot started! Bot ID: {bot.id}")
    global daily_report_task
    if settings.DAILY_STATS_ENABLED and settings.DAILY_STATS_CHAT_ID:
        if daily_report_task and not daily_report_task.done():
            logger.info("Daily report loop already running (skip duplicate on_startup)")
        else:
            daily_report_task = asyncio.create_task(_daily_report_loop(bot))
        if settings.DAILY_STATS_INTERVAL_MINUTES > 0:
            logger.info(
                "Daily report loop enabled chat_id=%s every %s minutes",
                settings.DAILY_STATS_CHAT_ID,
                settings.DAILY_STATS_INTERVAL_MINUTES,
            )
        else:
            times_local = _parse_times_local(getattr(settings, "DAILY_STATS_TIMES_LOCAL", ""))
            if times_local:
                pretty = ", ".join(f"{h:02d}:{m:02d}" for h, m in times_local)
                logger.info(
                    "Daily report loop enabled chat_id=%s at %s (%s)",
                    settings.DAILY_STATS_CHAT_ID,
                    pretty,
                    settings.APP_TIMEZONE,
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


def _parse_times_local(raw: str) -> list[tuple[int, int]]:
    value = (raw or "").strip()
    if not value:
        return []
    times: list[tuple[int, int]] = []
    for part in value.split(","):
        p = part.strip()
        if not p:
            continue
        hh, mm = p.split(":", 1)
        h = int(hh)
        m = int(mm)
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError(f"Invalid time: {p}")
        times.append((h, m))
    # de-dup + keep stable order
    seen: set[tuple[int, int]] = set()
    uniq: list[tuple[int, int]] = []
    for t in times:
        if t in seen:
            continue
        uniq.append(t)
        seen.add(t)
    return uniq


def _seconds_until_next_run_local(times: list[tuple[int, int]], *, tz_name: str) -> float:
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz=tz)
    candidates: list[datetime] = []
    for hour, minute in times:
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target = target + timedelta(days=1)
        candidates.append(target)
    if not candidates:
        return 1.0
    next_target = min(candidates)
    return max((next_target - now).total_seconds(), 1.0)


async def _daily_report_loop(bot: Bot):
    while True:
        if settings.DAILY_STATS_INTERVAL_MINUTES > 0:
            delay = max(float(settings.DAILY_STATS_INTERVAL_MINUTES) * 60.0, 10.0)
        else:
            times_local = _parse_times_local(getattr(settings, "DAILY_STATS_TIMES_LOCAL", ""))
            if times_local:
                delay = _seconds_until_next_run_local(times_local, tz_name=settings.APP_TIMEZONE)
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
    # Longer HTTP timeout: slow/unstable routes to api.telegram.org caused bot exit after default ~60s.
    session = AiohttpSession(timeout=120.0)
    bot = Bot(
        token=settings.BOT_TOKEN,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    
    # ✅ Redis для FSM storage
    redis = from_url(settings.REDIS_URL)
    storage = RedisStorage(redis=redis)
    
    dp = Dispatcher(storage=storage)  # ✅ Передаём storage

    # Anti double-click: one request at a time per user
    dp.message.middleware(RequestGuardMiddleware())
    dp.callback_query.middleware(RequestGuardMiddleware())
    
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
        tg_id = message.from_user.id if message.from_user else None
        if tg_id is not None:
            cancel_current_task(tg_id=tg_id)
        await state.clear()
        await message.answer("❌ Операция отменена", reply_markup=get_main_menu_keyboard())

    @dp.message(F.text == "❌ Отмена")
    async def cancel_button_cmd(message: types.Message, state):
        tg_id = message.from_user.id if message.from_user else None
        if tg_id is None:
            await message.answer("ℹ️ Нет активной операции.", reply_markup=get_main_menu_keyboard())
            return

        # Cancel any running request (including /status which is not FSM-based).
        cancelled = cancel_current_task(tg_id=tg_id)
        current = await state.get_state()
        if current is None and not cancelled and not has_running_task(tg_id=tg_id):
            await message.answer("ℹ️ Нет активной операции.", reply_markup=get_main_menu_keyboard())
            return
        await state.clear()
        await message.answer("❌ Операция отменена", reply_markup=get_main_menu_keyboard())
    
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
    backoff = 5.0
    while True:
        try:
            await dp.start_polling(bot)
            break
        except TelegramNetworkError as exc:
            logger.warning(
                "Telegram network error (%s), retrying in %.0fs",
                exc,
                backoff,
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2.0, 120.0)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot interrupted by user")