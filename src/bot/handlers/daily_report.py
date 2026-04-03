import asyncio

from aiogram import Router, types
from aiogram.filters import Command

from src.bot.keyboards.main_menu import get_main_menu_keyboard
from src.core.report_formatting import SERVICE_MESSAGE_DELAY_SEC
from src.services.daily_report import build_daily_group_report

router = Router()


@router.message(Command("dailyreport"))
async def cmd_daily_report(message: types.Message):
    parts = await build_daily_group_report()
    n = len(parts)
    for i, part in enumerate(parts):
        if i > 0:
            await asyncio.sleep(SERVICE_MESSAGE_DELAY_SEC)
        await message.answer(
            part,
            reply_markup=get_main_menu_keyboard() if i == n - 1 else None,
        )

