from datetime import datetime

from aiogram import F, Router, types
from aiogram.filters import Command
from redis.asyncio import from_url
from sqlalchemy import text

from src.bot.keyboards.main_menu import get_main_menu_keyboard
from src.core.config import settings
from src.core.logger import setup_logger
from src.database.session import async_session_maker

logger = setup_logger(__name__)
router = Router()

_started_at = datetime.utcnow()


async def _check_db() -> tuple[bool, str]:
    try:
        async with async_session_maker() as session:
            await session.execute(text("SELECT 1"))
        return True, "ok"
    except Exception as exc:
        logger.error("Healthcheck DB failed: %s", exc)
        return False, f"{type(exc).__name__}: {exc}"


async def _check_redis() -> tuple[bool, str]:
    redis = from_url(settings.REDIS_URL)
    try:
        pong = await redis.ping()
        return (bool(pong), "pong" if pong else "no pong")
    except Exception as exc:
        logger.error("Healthcheck Redis failed: %s", exc)
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        await redis.aclose()


async def _send_health(message: types.Message) -> None:
    db_ok, db_msg = await _check_db()
    redis_ok, redis_msg = await _check_redis()
    overall_ok = db_ok and redis_ok
    uptime = datetime.utcnow() - _started_at
    uptime_str = str(uptime).split(".")[0]

    status_icon = "✅" if overall_ok else "❌"
    report = (
        f"🩺 <b>Healthcheck</b>\n\n"
        f"{status_icon} Общий статус: {'OK' if overall_ok else 'DEGRADED'}\n"
        f"{'✅' if db_ok else '❌'} DB: {db_msg}\n"
        f"{'✅' if redis_ok else '❌'} Redis: {redis_msg}\n"
        f"⏱ Uptime: {uptime_str}"
    )
    await message.answer(report, reply_markup=get_main_menu_keyboard())


@router.message(Command("healthcheck"))
@router.message(F.text == "🩺 Healthcheck")
async def cmd_healthcheck(message: types.Message):
    await _send_health(message)

