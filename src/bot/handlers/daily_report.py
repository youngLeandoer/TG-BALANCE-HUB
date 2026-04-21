from aiogram import Router, types
from aiogram.filters import Command

from src.bot.keyboards.main_menu import get_main_menu_keyboard
from src.services.daily_report import build_daily_group_report

router = Router()


@router.message(Command("dailyreport"))
async def cmd_daily_report(message: types.Message):
    placeholder = await message.answer("📊 <b>Ежедневная сводка</b>\n⏳ Собираю данные…")
    parts = await build_daily_group_report()
    try:
        await placeholder.delete()
    except Exception:
        pass
    last_idx = len(parts) - 1
    for i, part in enumerate(parts):
        await message.answer(
            part,
            reply_markup=get_main_menu_keyboard() if i == last_idx else None,
        )
