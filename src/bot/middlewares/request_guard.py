from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from src.bot.request_guard import get_guard_lock


def _title_for_event(event: TelegramObject) -> str:
    if isinstance(event, Message):
        text = (event.text or "").strip()
        if text.startswith("/status") or text == "📊 Статус сервисов":
            return "Статус"
        if text.startswith("/stats") or text.startswith("/stats_export") or text.startswith("📉"):
            return "Статистика"
        if text.startswith("/dailyreport"):
            return "Ежедневная сводка"
        if text.startswith("/remove") or text == "🗑 Удалить сервис":
            return "Удаление"
        if text.startswith("/add") or text == "➕ Добавить сервис":
            return "Добавление"
        if text.startswith("/chatid"):
            return "Chat ID"
        if text.startswith("/healthcheck"):
            return "Healthcheck"
        if text.startswith("/help"):
            return "Help"
        if text.startswith("/start"):
            return "Start"
        if text.startswith("/cancel"):
            return "Cancel"
    return "Запрос"


def _should_bypass(event: TelegramObject) -> bool:
    # Allow /cancel to work even if something is running.
    if isinstance(event, Message):
        text = (event.text or "").strip()
        if text.startswith("/cancel"):
            return True
    return False


class RequestGuardMiddleware(BaseMiddleware):
    """
    Prevent parallel requests per user (anti double-click).

    Uses in-memory lock keyed by tg_id; process-local.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_id: int | None = None
        if isinstance(event, Message) and event.from_user:
            tg_id = event.from_user.id
        elif isinstance(event, CallbackQuery) and event.from_user:
            tg_id = event.from_user.id

        if tg_id is None or _should_bypass(event):
            return await handler(event, data)

        lock = get_guard_lock(tg_id=tg_id, name="__global__")
        if lock.locked():
            title = _title_for_event(event)
            text = f"⏳ <b>{title}</b>\n\nЗапрос уже выполняется, подождите немного…"
            if isinstance(event, Message):
                await event.answer(text)
            elif isinstance(event, CallbackQuery):
                # Acknowledge the click and show a message.
                await event.answer(text, show_alert=False)
                if event.message:
                    await event.message.answer(text)
            return None

        async with lock:
            return await handler(event, data)

