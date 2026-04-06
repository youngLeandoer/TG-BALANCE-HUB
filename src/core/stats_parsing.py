"""Shared period parsing and service titles for /stats and the HTTP API."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from src.database.models import Service

NON_MONETARY_SERVICES = {"wazzup", "yandex_geocoder"}
CURRENCY_ALIASES = {
    "RUB": "RUB",
    "RUR": "RUB",
    "RUBLES": "RUB",
    "RUBLE": "RUB",
    "РУБ": "RUB",
    "USD": "USD",
    "USDT": "USD",
}


def _display_base(service_name: str) -> str:
    special = {
        "yandex_geocoder": "Yandex Geocoder",
        "yandex_cloud": "Yandex Cloud",
        "timewebcloud": "Timeweb Cloud",
        "hosterby": "Hoster.by",
    }
    if service_name in special:
        return special[service_name]
    if "_" in service_name:
        return " ".join(p.capitalize() for p in service_name.split("_"))
    return service_name.title()


def _service_title(service: Service, *, index: int, total: int) -> str:
    if service.service_name == "adminvps_scraper":
        base = "AdminVPS"
    elif service.service_name == "atlex_scraper":
        base = "ATLEX"
    elif service.service_name == "nic_scraper":
        base = "NIC.RU"
    else:
        base = _display_base(service.service_name)
    credentials = service.credentials or {}
    if isinstance(credentials, str):
        try:
            credentials = json.loads(credentials)
        except Exception:
            credentials = {}
    label = credentials.get("label") if isinstance(credentials, dict) else None
    if label:
        return f"{base} ({label})"
    if total > 1:
        return f"{base} (аккаунт {index}/{total})"
    return base


def _normalize_currency(raw: str | None) -> str:
    value = (raw or "").strip().upper()
    if not value:
        return "UNK"
    return CURRENCY_ALIASES.get(value, value)


def _is_monetary_service(service_name: str) -> bool:
    return service_name not in NON_MONETARY_SERVICES


def _parse_since(raw_arg: str | None) -> tuple[datetime | None, str, str]:
    if not raw_arg:
        return datetime.utcnow() - timedelta(days=3), "3 дня", "3d"
    arg = raw_arg.strip().lower()
    if arg in {"all", "alltime", "все", "всё"}:
        return None, "всё время", "all"
    if arg == "month":
        now = datetime.utcnow()
        return datetime(year=now.year, month=now.month, day=1), "текущий месяц", "month"
    if arg.isdigit() and int(arg) > 0:
        days = int(arg)
        return datetime.utcnow() - timedelta(days=days), f"{days} дней", f"{days}d"
    return datetime.utcnow() - timedelta(days=3), "3 дня", "3d"


def _parse_stats_args(raw_args: list[str]) -> tuple[str | None, str | None]:
    service_filter = None
    period_arg = None
    if len(raw_args) == 1:
        single = raw_args[0].strip().lower()
        if single in {"month", "all", "alltime", "все", "всё"} or single.isdigit():
            period_arg = raw_args[0]
        else:
            service_filter = raw_args[0].strip().lower()
    elif len(raw_args) >= 2:
        service_filter = raw_args[0].strip().lower()
        period_arg = raw_args[1]
    return service_filter, period_arg
