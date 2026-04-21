from __future__ import annotations

from collections import defaultdict
import asyncio
from datetime import datetime, timedelta
import json
import html

from sqlalchemy import select
from src.core.config import settings
from src.core.logger import setup_logger
from src.core.report_formatting import chunk_blocks
from src.core.timezone import format_app_dt, to_app_tz
from src.database.models import BalanceHistory, Service, User
from src.services.base import connector_wait_timeout_seconds
from src.database.session import async_session_maker
from src.core.workspace import get_or_create_workspace_user_with_services
from src.services.providers.avito import AvitoConnector
from src.services.providers.hosterby import HosterByConnector
from src.services.providers.regru import RegRuConnector
from src.services.providers.smsaero import SMSAeroConnector
from src.services.providers.umnico import UmnicoConnector
from src.services.providers.wazzup import WazzupConnector
from src.services.providers.timewebcloud import TimewebCloudConnector
from src.services.providers.selectel import SelectelConnector
from src.services.local_scrapers import run_local_scrapers_if_enabled
from src.services.providers.yandex_cloud import YandexCloudConnector
from src.services.providers.yandex_geocoder import YandexGeocoderConnector

logger = setup_logger(__name__)

NON_MONETARY_SERVICES = {"wazzup", "yandex_geocoder"}
CURRENCY_ALIASES = {"RUR": "RUB", "RUB": "RUB", "RUBLES": "RUB", "RUBLE": "RUB", "РУБ": "RUB"}
SERVICE_CONNECTORS = {
    "umnico": UmnicoConnector,
    "avito": AvitoConnector,
    "wazzup": WazzupConnector,
    "regru": RegRuConnector,
    "hosterby": HosterByConnector,
    "smsaero": SMSAeroConnector,
    "timewebcloud": TimewebCloudConnector,
    "selectel": SelectelConnector,
    "yandex_cloud": YandexCloudConnector,
    "yandex_geocoder": YandexGeocoderConnector,
}


def _normalize_currency(raw: str | None) -> str:
    value = (raw or "").strip().upper()
    if not value:
        return "UNK"
    return CURRENCY_ALIASES.get(value, value)


def _is_monetary(service_name: str) -> bool:
    return service_name not in NON_MONETARY_SERVICES


def _service_title(service: Service, credentials: dict, idx: int, total: int) -> str:
    if service.service_name == "adminvps_scraper":
        base = "AdminVPS"
    elif service.service_name == "atlex_scraper":
        base = "ATLEX"
    elif service.service_name == "nic_scraper":
        base = "NIC.RU"
    else:
        base = service.service_name.title()
    label = credentials.get("label")
    if isinstance(label, str) and label.strip():
        return f"{base} ({label.strip()})"
    if total > 1:
        return f"{base} (аккаунт {idx}/{total})"
    return base


def _threshold_rub(credentials: dict) -> float:
    value = credentials.get("alert_threshold_rub")
    if value is None:
        return float(settings.LOW_BALANCE_THRESHOLD_RUB)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(settings.LOW_BALANCE_THRESHOLD_RUB)


def _safe_text(value: object) -> str:
    return html.escape(str(value), quote=False)


def _fmt_amount(value: float) -> str:
    try:
        return f"{float(value):.2f}"
    except Exception:
        return _safe_text(value)


def _record_snapshot(session, service_id: int, balance: float, currency: str, status: str = "OK") -> None:
    session.add(
        BalanceHistory(
            service_id=service_id,
            balance=balance,
            currency=currency,
            status=status,
            checked_at=datetime.utcnow(),
        )
    )


async def _refresh_current_balances(
    services: list[tuple[User, Service, dict]],
) -> dict[int, dict]:
    """
    Pull fresh balances before building report so group gets up-to-date data.
    Mirrors /status behavior but without sending per-service messages.

    Returns mapping service_id → extras dict (e.g. umnico channels, expiration)
    so report builder can render the same rich block as /status.
    """
    # If local Playwright scrapers are configured to run on the server,
    # run them once before hitting API providers so their pushed snapshots
    # are included in the final aggregated report.
    await run_local_scrapers_if_enabled(reason="daily_report_pre_refresh")

    sem = asyncio.Semaphore(6)

    async def _fetch_one(
        service: Service, credentials: dict
    ) -> tuple[int, float, str, dict] | None:
        if service.service_name in {"adminvps_scraper", "atlex_scraper", "nic_scraper"}:
            if "manual_balance" in credentials:
                try:
                    bal = float(credentials.get("manual_balance", 0.0))
                except (TypeError, ValueError):
                    return None
                cur = str(credentials.get("manual_currency", "RUB"))
                return (service.id, bal, cur, {})
            return None

        connector_cls = SERVICE_CONNECTORS.get(service.service_name)
        if not connector_cls:
            return None

        async with sem:
            connector = connector_cls(credentials=credentials)
            balance_data = await asyncio.wait_for(
                connector.get_balance_data(),
                timeout=connector_wait_timeout_seconds(service.service_name),
            )
        if balance_data.status != "OK":
            return None

        extras: dict = {}
        if getattr(balance_data, "expiration", None):
            extras["expiration"] = balance_data.expiration
        if service.service_name == "umnico":
            raw = balance_data.error_message or ""
            if isinstance(raw, str) and raw.strip().startswith("{"):
                try:
                    payload = json.loads(raw)
                    if isinstance(payload, dict):
                        extras["umnico"] = payload
                except Exception:
                    pass
        return (service.id, float(balance_data.balance), str(balance_data.currency), extras)

    tasks: list[asyncio.Task[tuple[int, float, str, dict] | None]] = []
    for _, service, credentials in services:
        tasks.append(asyncio.create_task(_fetch_one(service, credentials)))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    extras_by_service_id: dict[int, dict] = {}
    async with async_session_maker() as session:
        for res, (_, service, _) in zip(results, services):
            if isinstance(res, Exception):
                logger.warning(
                    "Daily pre-refresh failed service_id=%s name=%s err=%s",
                    service.id,
                    service.service_name,
                    res,
                )
                continue
            if res is None:
                continue
            service_id, bal, cur, extras = res
            if extras:
                extras_by_service_id[service_id] = extras
            if service.service_name in {"adminvps_scraper", "atlex_scraper", "nic_scraper"}:
                continue
            _record_snapshot(session, service_id, bal, cur, status="OK")
        await session.commit()

    return extras_by_service_id


async def build_daily_group_report() -> list[str]:
    now_utc = datetime.utcnow()
    since = now_utc - timedelta(days=1)

    async with async_session_maker() as session:
        user = await get_or_create_workspace_user_with_services(session)

        services = []
        for service in user.services:
            if not service.is_active or service.service_name == "mango_scraper":
                continue
            credentials = service.credentials or {}
            if isinstance(credentials, str):
                try:
                    credentials = json.loads(credentials)
                except Exception:
                    credentials = {}
            if not isinstance(credentials, dict):
                credentials = {}
            services.append((user, service, credentials))

        if not services:
            return ["📊 <b>Ежедневная сводка</b>\n\nНет активных сервисов."]

    # 1) First refresh current balances from providers/manual sources.
    extras_by_service_id = await _refresh_current_balances(services)

    # 2) Then build report from persisted history (now includes fresh snapshots).
    async with async_session_maker() as session:
        service_ids = [service.id for _, service, _ in services]
        rows = (
            await session.execute(
                select(BalanceHistory)
                .where(
                    BalanceHistory.service_id.in_(service_ids),
                    BalanceHistory.status == "OK",
                    BalanceHistory.checked_at >= since,
                )
                .order_by(BalanceHistory.service_id.asc(), BalanceHistory.checked_at.asc())
            )
        ).scalars().all()

    rows_by_service: dict[int, list[BalanceHistory]] = defaultdict(list)
    for row in rows:
        rows_by_service[row.service_id].append(row)

    totals_by_currency = defaultdict(lambda: {"spend": 0.0, "topup": 0.0})
    low_balance_alerts: list[str] = []
    # (sort_key, block). 0 = ❌ no data, 1 = ⚠️ low, 3 = ✅ ok
    ranked_blocks: list[tuple[int, str]] = []
    total_rub_balance = 0.0
    has_rub = False
    ok_count = warn_count = err_count = 0

    totals_per_user_service = defaultdict(int)
    for user, service, _ in services:
        totals_per_user_service[(user.id, service.service_name)] += 1
    seen_per_user_service = defaultdict(int)

    for user, service, credentials in services:
        seen_per_user_service[(user.id, service.service_name)] += 1
        title = _service_title(
            service,
            credentials,
            seen_per_user_service[(user.id, service.service_name)],
            totals_per_user_service[(user.id, service.service_name)],
        )
        service_rows = rows_by_service.get(service.id, [])
        if not service_rows:
            ranked_blocks.append((
                0,
                f"❌ <b>{_safe_text(title)}</b>\n"
                f"Причина: <code>нет замеров за 24ч</code>",
            ))
            err_count += 1
            continue

        last = service_rows[-1]
        if _is_monetary(service.service_name):
            spend = 0.0
            topup = 0.0
            for prev, curr in zip(service_rows, service_rows[1:]):
                delta = float(curr.balance) - float(prev.balance)
                if delta < 0:
                    spend += abs(delta)
                elif delta > 0:
                    topup += delta

            currency = _normalize_currency(last.currency)
            threshold = _threshold_rub(credentials)
            is_low = currency == "RUB" and float(last.balance) <= threshold
            head_icon = "⚠️" if is_low else "✅"
            lines = [
                f"{head_icon} <b>{_safe_text(title)}</b>",
                f"Баланс: <code>{_fmt_amount(last.balance)} {currency}</code>",
                f"Расход: <code>{spend:.2f}</code> · Пополнения: <code>{topup:.2f}</code>",
            ]

            extras = extras_by_service_id.get(service.id, {})
            if service.service_name == "umnico":
                umnico_payload = extras.get("umnico") or {}
                active = umnico_payload.get("channels_active")
                total = umnico_payload.get("channels_total")
                active_list = umnico_payload.get("active_channels")
                active_more = int(umnico_payload.get("active_channels_more") or 0)
                inactive_list = umnico_payload.get("inactive_channels")
                inactive_more = int(umnico_payload.get("inactive_channels_more") or 0)
                try:
                    active_int = int(active) if active is not None else None
                    total_int = int(total) if total is not None else None
                except (TypeError, ValueError):
                    active_int = total_int = None
                if active_int is not None and total_int is not None:
                    lines.append(f"Каналы: <b>{active_int}/{total_int}</b> активны")
                if active_list:
                    lines.append("✅ <b>Активные</b>:")
                    lines.extend([f"  • <code>{_safe_text(ch)}</code>" for ch in active_list])
                    if active_more > 0:
                        lines.append(f"  • …и ещё <b>{active_more}</b>")
                if inactive_list:
                    lines.append("⛔ <b>Неактивные</b>:")
                    lines.extend([f"  • <code>{_safe_text(ch)}</code>" for ch in inactive_list])
                    if inactive_more > 0:
                        lines.append(f"  • …и ещё <b>{inactive_more}</b>")

            if is_low:
                lines.append(f"⚠️ Ниже порога: <code>{threshold:.2f} RUB</code>")

            expiration = extras.get("expiration")
            if expiration:
                expiration_dt = to_app_tz(expiration)
                lines.append(
                    f"Оплачено до: <code>{expiration_dt.strftime('%d.%m.%Y %H:%M %Z')}</code>"
                )

            if is_low:
                low_balance_alerts.append(
                    f"⚠️ <b>{_safe_text(title)}</b>: <code>{_fmt_amount(last.balance)} RUB</code> "
                    f"(порог <code>{threshold:.2f}</code>)"
                )
                warn_count += 1
            else:
                ok_count += 1
            totals_by_currency[currency]["spend"] += spend
            totals_by_currency[currency]["topup"] += topup
            if currency == "RUB":
                total_rub_balance += float(last.balance)
                has_rub = True

            ranked_blocks.append((1 if is_low else 3, "\n".join(lines)))
        else:
            diff = 0.0
            if len(service_rows) >= 2:
                diff = float(service_rows[-1].balance) - float(service_rows[0].balance)
            ranked_blocks.append((
                3,
                f"✅ <b>{_safe_text(title)}</b>\n"
                f"Активные каналы: <b>{int(last.balance)}</b> (изменение <code>{diff:+.0f}</code>)",
            ))
            ok_count += 1

    ranked_blocks.sort(key=lambda p: p[0])
    service_blocks = [b for _, b in ranked_blocks]

    summary_parts = [f"✅ {ok_count}"]
    if warn_count:
        summary_parts.append(f"⚠️ {warn_count}")
    if err_count:
        summary_parts.append(f"❌ {err_count}")
    header_lines = [
        f"📊 <b>Ежедневная сводка</b> · {' · '.join(summary_parts)}",
        f"Сформировано: <code>{_safe_text(format_app_dt(now_utc))}</code>",
    ]
    if has_rub:
        header_lines.append(f"Суммарно: <b>{_fmt_amount(total_rub_balance)} RUB</b>")
    header = "\n".join(header_lines)

    totals_lines = ["📊 <b>Итого по валютам</b>"]
    if not totals_by_currency:
        totals_lines.append("• Нет денежных движений за период")
    else:
        for cur in sorted(totals_by_currency.keys()):
            totals_lines.append(
                f"• {_safe_text(cur)}: расход <code>{totals_by_currency[cur]['spend']:.2f}</code>, "
                f"пополнения <code>{totals_by_currency[cur]['topup']:.2f}</code>"
            )
    totals_block = "\n".join(totals_lines)

    all_blocks = [header, *service_blocks, totals_block]
    if low_balance_alerts:
        all_blocks.append("<b>Пороговые алерты</b>\n" + "\n".join(low_balance_alerts))

    return chunk_blocks(all_blocks)

