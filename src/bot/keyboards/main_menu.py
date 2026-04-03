from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


def get_main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Добавить сервис"), KeyboardButton(text="📊 Статус сервисов")],
            [KeyboardButton(text="🗑 Удалить сервис")],
            [KeyboardButton(text="📉 Статистика (3 дня)"), KeyboardButton(text="📅 Статистика (месяц)")],
            [KeyboardButton(text="🗂 Статистика (всё время)")],
            [KeyboardButton(text="📤 Экспорт CSV (3 дня)"), KeyboardButton(text="📤 Экспорт CSV (месяц)")],
            [KeyboardButton(text="📤 Экспорт CSV (всё время)")],
            [KeyboardButton(text="ℹ️ Help")],
            [KeyboardButton(text="🩺 Healthcheck")],
            [KeyboardButton(text="❌ Отмена")],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Выберите действие...",
    )

