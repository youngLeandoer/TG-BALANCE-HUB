from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from src.core.config import settings


def app_tz() -> ZoneInfo:
    return ZoneInfo(settings.APP_TIMEZONE)


def to_app_tz(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(app_tz())


def parse_iso_as_utc(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def format_app_dt(dt: datetime, with_tz: bool = True) -> str:
    local_dt = to_app_tz(dt)
    if with_tz:
        return local_dt.strftime("%d.%m.%Y %H:%M %Z")
    return local_dt.strftime("%d.%m.%Y %H:%M")

