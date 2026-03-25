from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def get_services_keyboard() -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Umnico", callback_data="add_service_umnico")],
        [InlineKeyboardButton(text="🟠 Avito", callback_data="add_service_avito")],
        [InlineKeyboardButton(text="🟣 Wazzup", callback_data="add_service_wazzup")],
        [InlineKeyboardButton(text="🌐 REG.RU", callback_data="add_service_regru")],
        [InlineKeyboardButton(text="🇧🇾 Hoster.by", callback_data="add_service_hosterby")],
        [InlineKeyboardButton(text="✉️ SMS Aero", callback_data="add_service_smsaero")],
        [InlineKeyboardButton(text="🖥️ AdminVPS (локальный баланс)", callback_data="add_service_adminvps_scraper")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="add_service_cancel")],
    ])
    return keyboard


def get_cancel_keyboard() -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="add_service_cancel")],
    ])
    return keyboard