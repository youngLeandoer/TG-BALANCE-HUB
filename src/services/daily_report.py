from __future__ import annotations

from collections import defaultdict
import asyncio
from datetime import datetime, timedelta
import json
import html

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.core.config import settings
from src.core.logger import setup_logger
from src.core.timezone import format_app_dt
from src.database.models import BalanceHistory, Service, User
from src.database.session import async_session_maker
from src.services.providers.avito import AvitoConnector
from src.services.providers.hosterby import HosterByConnector
from src.services.providers.regru import RegRuConnector
from src.services.providers.smsaero import SMSAeroConnector
from src.services.providers.umnico import UmnicoConnector
from src.services.providers.wazzup import WazzupConnector
from src.services.providers.timewebcloud import TimewebCloudConnector

logger = setup_logger(__name__)

NON_MONETARY_SERVICES = {"wazzup"}
CURRENCY_ALIASES = {"RUR": "RUB", "RUB": "RUB", "RUBLES": "RUB", "RUBLE": "RUB", "РУБ": "RUB"}
SERVICE_CONNECTORS = {
    "umnico": UmnicoConnector,
    "avito": AvitoConnector,
    "wazzup": WazzupConnector,
    "regru": RegRuConnector,
    "hosterby": HosterByConnector,
    "smsaero": SMSAeroConnector,
    "timewebcloud": TimewebCloudConnector,
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


async def _refresh_current_balances(services: list[tuple[User, Service, dict]]) -> None:
    """
    Pull fresh balances before building report so group gets up-to-date data.
    Mirrors /status behavior but without sending per-service messages.
    """
    async with async_session_maker() as session:
        for _, service, credentials in services:
            if service.service_name in {"adminvps_scraper", "atlex_scraper", "nic_scraper"}:
                if "manual_balance" in credentials:
                    try:
                        bal = float(credentials.get("manual_balance", 0.0))
                    except (TypeError, ValueError):
                        continue
                    cur = str(credentials.get("manual_currency", "RUB"))
                    _record_snapshot(session, service.id, bal, cur, status="OK")
                continue

            connector_cls = SERVICE_CONNECTORS.get(service.service_name)
            if not connector_cls:
                continue
            try:
                connector = connector_cls(credentials=credentials)
                balance_data = await asyncio.wait_for(connector.get_balance_data(), timeout=12.0)
                if balance_data.status == "OK":
                    _record_snapshot(
                        session,
                        service.id,
                        float(balance_data.balance),
                        str(balance_data.currency),
                        status="OK",
                    )
            except Exception as exc:
                logger.warning(
                    "Daily pre-refresh failed service_id=%s name=%s err=%s",
                    service.id,
                    service.service_name,
                    exc,
                )
                continue
        await session.commit()


async def build_daily_group_report() -> str:
    now_utc = datetime.utcnow()
    since = now_utc - timedelta(days=1)

    async with async_session_maker() as session:
        users = (
            await session.execute(select(User).options(selectinload(User.services)))
        ).scalars().all()

        services = []
        for user in users:
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
            return "📊 <b>Ежедневная сводка</b>\n\nНет активных сервисов."

    # 1) First refresh current balances from providers/manual sources.
    await _refresh_current_balances(services)

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
    user_blocks: dict[int, list[str]] = defaultdict(list)

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
            user_blocks[user.id].append(f"• {title}: <i>нет замеров за 24ч</i>")
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
            user_blocks[user.id].append(
                "• "
                f"<b>{title}</b>\n"
                f"  Баланс: <code>{_fmt_amount(last.balance)} {currency}</code>\n"
                f"  Расход: <code>{spend:.2f}</code> | Пополнения: <code>{topup:.2f}</code>"
            )
            totals_by_currency[currency]["spend"] += spend
            totals_by_currency[currency]["topup"] += topup

            threshold = _threshold_rub(credentials)
            if currency == "RUB" and float(last.balance) <= threshold:
                low_balance_alerts.append(
                    f"⚠️ <b>{_safe_text(title)}</b>: <code>{_fmt_amount(last.balance)} RUB</code> (порог <code>{threshold:.2f}</code>)"
                )
        else:
            diff = 0.0
            if len(service_rows) >= 2:
                diff = float(service_rows[-1].balance) - float(service_rows[0].balance)
            user_blocks[user.id].append(
                "• "
                f"<b>{title}</b>\n"
                f"  Активные каналы: <b>{int(last.balance)}</b> (изменение <code>{diff:+.0f}</code>)"
            )

    report = (
        "📊 <b>Ежедневная сводка</b>\n"
        "Период: последние 24ч (MSK)\n"
        f"Сформировано: {format_app_dt(now_utc)}\n\n"
    )

    for user in users:
        blocks = user_blocks.get(user.id)
        if not blocks:
            continue
        uname = f"@{user.username}" if user.username else f"tg_id={user.tg_id}"
        report += f"<b>👤 {uname}</b>\n" + "\n\n".join(blocks) + "\n\n"

    report += "<b>Итого по валютам</b>\n"
    if not totals_by_currency:
        report += "• Нет денежных движений за период\n"
    else:
        for cur in sorted(totals_by_currency.keys()):
            report += (
                f"• {cur}: расход {totals_by_currency[cur]['spend']:.2f}, "
                f"пополнения {totals_by_currency[cur]['topup']:.2f}\n"
            )

    if low_balance_alerts:
        report += "\n<b>Пороговые алерты</b>\n" + "\n".join(low_balance_alerts)

    if len(report) > 3900:
        report = report[:3850] + "\n\n…(сокращено)"
    return report

