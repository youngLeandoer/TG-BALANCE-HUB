from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def get_services_keyboard() -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Umnico", callback_data="add_service_umnico")],
        [InlineKeyboardButton(text="🌊 DigitalOcean", callback_data="add_service_digitalocean")],
        [InlineKeyboardButton(text="☁️ AWS", callback_data="add_service_aws")],
        [InlineKeyboardButton(text="🔷 Hetzner", callback_data="add_service_hetzner")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="add_service_cancel")],
    ])
    return keyboard


def get_cancel_keyboard() -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="add_service_cancel")],
    ])
    return keyboard