from aiogram import Router, types
from aiogram.filters import Command

from src.bot.keyboards.main_menu import get_main_menu_keyboard

router = Router()


@router.message(Command("chatid"))
async def cmd_chat_id(message: types.Message):
    chat = message.chat
    text = (
        "🆔 <b>Chat ID</b>\n\n"
        f"ID: <code>{chat.id}</code>\n"
        f"Тип: <code>{chat.type}</code>\n"
    )
    await message.answer(text, reply_markup=get_main_menu_keyboard())

