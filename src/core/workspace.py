"""Единый «рабочий» пользователь в БД: все сервисы и отчёты общие для чата."""

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.core.config import settings
from src.database.models import User


async def get_workspace_user(session):
    """Пользователь-владелец общего набора сервисов или None."""
    r = await session.execute(select(User).where(User.tg_id == settings.shared_workspace_tg_id))
    return r.scalar_one_or_none()


async def get_or_create_workspace_user(session) -> User:
    """Тот же пользователь; при отсутствии строки в БД — создаётся."""
    user = await get_workspace_user(session)
    if user is None:
        user = User(tg_id=settings.shared_workspace_tg_id, username=None)
        session.add(user)
        await session.flush()
    return user


async def get_or_create_workspace_user_with_services(session) -> User:
    """Как get_or_create_workspace_user, сразу с загруженным services."""
    r = await session.execute(
        select(User)
        .where(User.tg_id == settings.shared_workspace_tg_id)
        .options(selectinload(User.services))
    )
    user = r.scalar_one_or_none()
    if user is None:
        user = User(tg_id=settings.shared_workspace_tg_id, username=None)
        session.add(user)
        await session.flush()
        await session.refresh(user, ["services"])
    return user
