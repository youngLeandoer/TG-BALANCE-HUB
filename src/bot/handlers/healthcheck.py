import asyncio
from datetime import datetime

from aiogram import Router, types
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

_CHECK_TIMEOUT_SEC = 5.0

# Satellite vs stethoscope: different Telegram builds / old keyboards used different emoji.
_SAT = "\U0001f4e1"
_STETH = "\U0001fa7a"
_HEALTHCHECK_LABELS = frozenset(
    {
        f"{_SAT} Healthcheck",
        f"{_STETH} Healthcheck",
        "Healthcheck",
        "healthcheck",
    }
)


def _looks_like_healthcheck_button(text: str | None) -> bool:
    if not text:
        return False
    t = text.strip()
    if t in _HEALTHCHECK_LABELS:
        return True
    if t.lower() == "healthcheck":
        return True
    if len(t) > 32:
        return False
    return t.endswith("Healthcheck")


async def _check_db() -> tuple[bool, str]:
    try:
        async with async_session_maker() as session:
            await asyncio.wait_for(session.execute(text("SELECT 1")), timeout=_CHECK_TIMEOUT_SEC)
        return True, "ok"
    except asyncio.TimeoutError:
        logger.error("Healthcheck DB timed out after %ss", _CHECK_TIMEOUT_SEC)
        return False, f"timeout after {_CHECK_TIMEOUT_SEC:.0f}s"
    except Exception as exc:
        logger.error("Healthcheck DB failed: %s", exc)
        return False, f"{type(exc).__name__}: {exc}"


async def _check_redis() -> tuple[bool, str]:
    redis = from_url(settings.REDIS_URL)
    try:
        pong = await asyncio.wait_for(redis.ping(), timeout=_CHECK_TIMEOUT_SEC)
        return (bool(pong), "pong" if pong else "no pong")
    except asyncio.TimeoutError:
        logger.error("Healthcheck Redis timed out after %ss", _CHECK_TIMEOUT_SEC)
        return False, f"timeout after {_CHECK_TIMEOUT_SEC:.0f}s"
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

    ok_emoji = "\u2705"
    bad_emoji = "\u274c"
    status_icon = ok_emoji if overall_ok else bad_emoji
    report = (
        f"{_STETH} <b>Healthcheck</b>\n\n"
        f"{status_icon} \u041e\u0431\u0449\u0438\u0439 \u0441\u0442\u0430\u0442\u0443\u0441: {'OK' if overall_ok else 'DEGRADED'}\n"
        f"{ok_emoji if db_ok else bad_emoji} DB: {db_msg}\n"
        f"{ok_emoji if redis_ok else bad_emoji} Redis: {redis_msg}\n"
        f"\u23f1 Uptime: {uptime_str}"
    )
    await message.answer(report, reply_markup=get_main_menu_keyboard())


@router.message(Command("healthcheck"))
@router.message(lambda m: _looks_like_healthcheck_button(m.text))
async def cmd_healthcheck(message: types.Message):
    await _send_health(message)
