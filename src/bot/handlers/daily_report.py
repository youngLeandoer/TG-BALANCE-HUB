from aiogram import Router, types
from aiogram.filters import Command

from src.bot.keyboards.main_menu import get_main_menu_keyboard
from src.services.daily_report import build_daily_group_report

router = Router()


@router.message(Command("dailyreport"))
async def cmd_daily_report(message: types.Message):
    report = await build_daily_group_report()
    await message.answer(report, reply_markup=get_main_menu_keyboard())

