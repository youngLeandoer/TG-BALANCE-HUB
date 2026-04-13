"""Удаление подключённого сервиса (общий список для всех)."""

from __future__ import annotations

import html
from collections import defaultdict

from aiogram import F, Router, types
from aiogram.filters import Command
from sqlalchemy import select
from src.core.stats_parsing import _service_title
from src.bot.keyboards.main_menu import get_main_menu_keyboard
from src.core.logger import setup_logger
from src.core.workspace import get_or_create_workspace_user_with_services, get_workspace_user
from src.database.models import Service, User
from src.database.session import async_session_maker

logger = setup_logger(__name__)
router = Router()

CB_PICK = "rms"  # rms_<id>
CB_YES = "rmy"  # rmy_<id>
CB_NO = "rmn"


def _safe(s: object) -> str:
    return html.escape(str(s), quote=False)


def _button_caption(service: Service, *, index: int, total: int) -> str:
    title = _service_title(service, index=index, total=total)
    text = f"{title}"
    if len(text) > 56:
        text = text[:53] + "…"
    return text


async def _load_workspace_services() -> tuple[User | None, list[Service]]:
    async with async_session_maker() as session:
        user = await get_or_create_workspace_user_with_services(session)
        if not user.services:
            return user, []
        services = [
            s
            for s in user.services
            if s.is_active and s.service_name != "mango_scraper"
        ]
        services.sort(key=lambda s: (s.service_name, s.id))
        return user, services


@router.message(Command("remove", "delete_service"))
@router.message(F.text == "🗑 Удалить сервис")
async def cmd_remove_start(message: types.Message):
    _user, services = await _load_workspace_services()
    if not services:
        await message.answer(
            "🗑 <b>Удаление сервиса</b>\n\n"
            "Нет активных сервисов для удаления.\n"
            "Добавьте сервис через <code>/add</code>.",
            reply_markup=get_main_menu_keyboard(),
        )
        return

    totals: dict[str, int] = defaultdict(int)
    for s in services:
        totals[s.service_name] += 1

    seen: dict[str, int] = defaultdict(int)
    buttons: list[list[types.InlineKeyboardButton]] = []
    for svc in services:
        seen[svc.service_name] += 1
        cap = _button_caption(svc, index=seen[svc.service_name], total=totals[svc.service_name])
        buttons.append(
            [
                types.InlineKeyboardButton(
                    text=f"🗑 {cap}",
                    callback_data=f"{CB_PICK}_{svc.id}",
                )
            ]
        )

    await message.answer(
        "🗑 <b>Удаление сервиса</b>\n\n"
        "Выберите сервис — затем подтвердите удаление.\n"
        "История балансов по этому сервису будет удалена.",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data.regexp(rf"^{CB_PICK}_\d+$"))
async def cb_remove_pick(callback: types.CallbackQuery):
    if not callback.data or not callback.from_user:
        await callback.answer()
        return
    try:
        service_id = int(callback.data.removeprefix(f"{CB_PICK}_"))
    except ValueError:
        await callback.answer("Некорректные данные", show_alert=True)
        return

    _user, services = await _load_workspace_services()
    svc = next((s for s in services if s.id == service_id), None)
    if not svc:
        await callback.answer("Сервис не найден", show_alert=True)
        return

    totals: dict[str, int] = defaultdict(int)
    for s in services:
        totals[s.service_name] += 1
    seen: dict[str, int] = defaultdict(int)
    title = ""
    for s in services:
        seen[s.service_name] += 1
        if s.id == service_id:
            title = _service_title(s, index=seen[s.service_name], total=totals[s.service_name])
            break

    await callback.message.edit_text(
        "🗑 <b>Подтверждение</b>\n\n"
        f"Удалить <b>{_safe(title)}</b>?\n"
        "Данные подключения и история балансов по нему будут <b>безвозвратно</b> удалены.",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text="✅ Да, удалить",
                        callback_data=f"{CB_YES}_{service_id}",
                    ),
                    types.InlineKeyboardButton(
                        text="❌ Отмена",
                        callback_data=CB_NO,
                    ),
                ]
            ]
        ),
    )

@router.callback_query(F.data == CB_NO)
async def cb_remove_cancel(callback: types.CallbackQuery):
    await callback.message.edit_text("❌ Удаление отменено.", reply_markup=None)
    await callback.answer()
    await callback.message.answer("Главное меню:", reply_markup=get_main_menu_keyboard())


@router.callback_query(F.data.regexp(rf"^{CB_YES}_\d+$"))
async def cb_remove_confirm(callback: types.CallbackQuery):
    if not callback.data or not callback.from_user:
        await callback.answer()
        return
    try:
        service_id = int(callback.data.removeprefix(f"{CB_YES}_"))
    except ValueError:
        await callback.answer("Ошибка данных", show_alert=True)
        return

    actor_tg_id = callback.from_user.id

    async with async_session_maker() as session:
        user = await get_workspace_user(session)
        if not user:
            await callback.answer("Сервис уже удалён или недоступен", show_alert=True)
            return
        result = await session.execute(
            select(Service).where(Service.user_id == user.id, Service.id == service_id)
        )
        svc = result.scalar_one_or_none()
        if not svc:
            await callback.answer("Сервис уже удалён или недоступен", show_alert=True)
            return

        siblings = (
            await session.execute(
                select(Service).where(Service.user_id == svc.user_id, Service.is_active.is_(True))
            )
        ).scalars().all()
        siblings = [s for s in siblings if s.service_name != "mango_scraper"]
        siblings.sort(key=lambda x: (x.service_name, x.id))
        totals: dict[str, int] = defaultdict(int)
        for s in siblings:
            totals[s.service_name] += 1
        seen: dict[str, int] = defaultdict(int)
        title = svc.service_name
        for s in siblings:
            seen[s.service_name] += 1
            if s.id == svc.id:
                title = _service_title(s, index=seen[s.service_name], total=totals[s.service_name])
                break

        await session.delete(svc)
        await session.commit()
        logger.info(
            "Service deleted actor_tg_id=%s service_id=%s name=%s",
            actor_tg_id,
            service_id,
            svc.service_name,
        )

    await callback.message.edit_text(
        f"✅ Сервис удалён: <b>{_safe(title)}</b>",
        reply_markup=None,
    )
    await callback.answer("Удалено")
    await callback.message.answer("Главное меню:", reply_markup=get_main_menu_keyboard())
