from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command
import asyncio
from datetime import datetime
import json
import html
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from src.database.models import User, Service, BalanceHistory
from src.database.session import async_session_maker
from src.core.config import settings
from src.core.logger import setup_logger
from src.core.timezone import format_app_dt, parse_iso_as_utc, to_app_tz
from src.bot.keyboards.main_menu import get_main_menu_keyboard
from src.services.providers.umnico import UmnicoConnector
from src.services.providers.hosterby import HosterByConnector
from src.services.providers.smsaero import SMSAeroConnector
from src.services.providers.avito import AvitoConnector
from src.services.providers.regru import RegRuConnector
from src.services.providers.wazzup import WazzupConnector
from src.services.providers.timewebcloud import TimewebCloudConnector
from src.services.providers.selectel import SelectelConnector

logger = setup_logger(__name__)
router = Router()
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
}


def _record_balance_snapshot(
    session,
    *,
    service_id: int,
    balance: float,
    currency: str,
    status: str = "OK",
) -> None:
    session.add(
        BalanceHistory(
            service_id=service_id,
            balance=balance,
            currency=currency,
            status=status,
            checked_at=datetime.utcnow(),
        )
    )


def _normalize_currency(raw: str | None) -> str:
    value = (raw or "").strip().upper()
    if not value:
        return "UNK"
    return CURRENCY_ALIASES.get(value, value)


def _resolve_alert_threshold_rub(credentials: dict) -> float:
    try:
        return float(credentials.get("alert_threshold_rub", settings.LOW_BALANCE_THRESHOLD_RUB))
    except (TypeError, ValueError):
        return float(settings.LOW_BALANCE_THRESHOLD_RUB)


def _safe_text(value: object) -> str:
    return html.escape(str(value), quote=False)


def _fmt_amount(amount: float) -> str:
    try:
        return f"{float(amount):.2f}"
    except Exception:
        return _safe_text(amount)


@router.message(Command("status"))
@router.message(F.text == "📊 Статус сервисов")
async def cmd_status(message: types.Message):
    tg_id = message.from_user.id
    logger.info(f"/status requested by tg_id={tg_id}")
    
    async with async_session_maker() as session:
        result = await session.execute(
            select(User)
            .where(User.tg_id == tg_id)
            .options(selectinload(User.services))
        )
        user = result.scalar_one_or_none()
        
        if not user or not user.services:
            await message.answer(
                "📊 <b>Статус сервисов</b>\n\n"
                "У вас пока нет добавленных сервисов.\n"
                "Используйте /add для добавления",
                reply_markup=get_main_menu_keyboard(),
            )
            return

        # Помечаем дубли (одинаковый service_name) как аккаунт N/M,
        # чтобы в отчете было видно: это не баг, а несколько подключений.
        service_totals: dict[str, int] = {}
        for svc in user.services:
            if not svc.is_active or svc.service_name == "mango_scraper":
                continue
            service_totals[svc.service_name] = service_totals.get(svc.service_name, 0) + 1
        service_seen: dict[str, int] = {}

        def build_service_title(service_name: str, label: str | None = None, base_title: str | None = None) -> str:
            title = base_title or service_name.title()
            if label:
                return f"{title} ({label})"
            total = service_totals.get(service_name, 1)
            if total > 1:
                current = service_seen.get(service_name, 0) + 1
                service_seen[service_name] = current
                return f"{title} (аккаунт {current}/{total})"
            return title

        blocks: list[str] = []
        
        for service in user.services:
            if not service.is_active:
                logger.info(f"Skip inactive service id={service.id} name={service.service_name}")
                continue

            # Temporarily disabled due to regional access restrictions.
            if service.service_name == "mango_scraper":
                logger.info(f"Skip temporarily disabled service id={service.id} name={service.service_name}")
                continue
            
            try:
                credentials = service.credentials
                if isinstance(credentials, str):
                    credentials = json.loads(credentials)

            except Exception as parse_error:
                logger.error(f"Credentials parse failed: {parse_error}")
                service_title = build_service_title(service.service_name)
                blocks.append(
                    "\n".join(
                        [
                            f"❌ <b>{service_title}</b>",
                            f"Причина: <code>{_safe_text(parse_error)}</code>",
                        ]
                    )
                )
                continue
            
            if service.service_name in {"adminvps_scraper", "atlex_scraper", "nic_scraper"}:
                base_title = (
                    "AdminVPS"
                    if service.service_name == "adminvps_scraper"
                    else ("ATLEX" if service.service_name == "atlex_scraper" else "NIC.RU")
                )
                service_title = build_service_title(
                    service.service_name,
                    credentials.get("label"),
                    base_title=base_title,
                )
                if "manual_balance" in credentials:
                    manual_balance = float(credentials.get("manual_balance", 0.0))
                    manual_currency = str(credentials.get("manual_currency", "RUB"))
                    threshold = _resolve_alert_threshold_rub(credentials)
                    lines = [
                        f"✅ <b>{service_title}</b>",
                        f"Баланс: <code>{_fmt_amount(manual_balance)} {_safe_text(manual_currency)}</code>",
                    ]
                    if _normalize_currency(manual_currency) == "RUB" and manual_balance <= threshold:
                        lines.append(f"⚠️ Ниже порога: <code>{threshold:.2f} RUB</code>")
                    manual_updated_at = credentials.get("manual_updated_at")
                    if isinstance(manual_updated_at, str) and manual_updated_at:
                        parsed = parse_iso_as_utc(manual_updated_at)
                        lines.append(
                            f"Обновлено: <code>{_safe_text(format_app_dt(parsed) if parsed else manual_updated_at)}</code>"
                        )
                    blocks.append("\n".join(lines))
                    _record_balance_snapshot(
                        session,
                        service_id=service.id,
                        balance=manual_balance,
                        currency=manual_currency,
                        status="OK",
                    )
                    service.last_check = datetime.utcnow()
                else:
                    helper = (
                        "tools/adminvps_local_browser.py"
                        if service.service_name == "adminvps_scraper"
                        else ("tools/atlex_local_browser.py" if service.service_name == "atlex_scraper" else "tools/nic_local_browser.py")
                    )
                    env_prefix = (
                        "ADMINVPS_LOCAL_*"
                        if service.service_name == "adminvps_scraper"
                        else ("ATLEX_LOCAL_*" if service.service_name == "atlex_scraper" else "NIC_LOCAL_*")
                    )
                    blocks.append(
                        "\n".join(
                            [
                                f"⏳ <b>{service_title}</b>",
                                "Ожидаю баланс с вашего ПК.",
                                f"Запуск: <code>{helper}</code>",
                                f"Переменные: <code>{env_prefix}</code> + <code>INTERNAL_UPDATE_TOKEN</code>",
                            ]
                        )
                    )
                continue

            connector_cls = SERVICE_CONNECTORS.get(service.service_name)
            if not connector_cls:
                logger.warning(f"No connector for service id={service.id} name={service.service_name}")
                service_title = build_service_title(service.service_name, credentials.get("label"))
                blocks.append(
                    "\n".join(
                        [
                            f"❌ <b>{service_title}</b>",
                            "Причина: <code>сервис пока не поддерживается</code>",
                        ]
                    )
                )
                continue

            logger.info(
                f"Checking service id={service.id} name={service.service_name} with connector={connector_cls.__name__}"
            )
            connector = connector_cls(credentials=credentials)
            try:
                # Один проблемный провайдер не должен подвешивать весь /status.
                balance_data = await asyncio.wait_for(connector.get_balance_data(), timeout=12.0)
            except asyncio.TimeoutError:
                logger.warning(f"Service check timeout id={service.id} name={service.service_name}")
                service_title = build_service_title(service.service_name, credentials.get("label"))
                blocks.append(
                    "\n".join(
                        [
                            f"❌ <b>{service_title}</b>",
                            "Причина: <code>таймаут запроса к API</code>",
                        ]
                    )
                )
                continue
            
            if balance_data.status == "OK":
                service_title = build_service_title(service.service_name, credentials.get("label"))
                threshold = _resolve_alert_threshold_rub(credentials)
                lines = [f"✅ <b>{service_title}</b>"]
                if service.service_name == "wazzup":
                    lines.append("Статус: <code>API доступен</code>")
                    lines.append(f"Активных каналов: <b>{int(balance_data.balance)}</b>")
                    unpaid_channels = 0
                    if (
                        isinstance(balance_data.error_message, str)
                        and balance_data.error_message.startswith("not_enough_money=")
                    ):
                        try:
                            unpaid_channels = int(balance_data.error_message.split("=", 1)[1])
                        except ValueError:
                            unpaid_channels = 0
                    lines.append(f"Каналов с неоплатой: <b>{unpaid_channels}</b>")
                elif service.service_name == "umnico":
                    lines.append(
                        f"Баланс: <code>{_fmt_amount(balance_data.balance)} {_safe_text(balance_data.currency)}</code>"
                    )
                    active = None
                    total = None
                    active_list: list[str] | None = None
                    active_more: int = 0
                    inactive_list: list[str] | None = None
                    inactive_more: int = 0
                    if isinstance(balance_data.error_message, str) and balance_data.error_message.strip():
                        raw = balance_data.error_message.strip()
                        # New format: JSON blob with active channel list.
                        if raw.startswith("{"):
                            try:
                                payload = json.loads(raw)
                                if isinstance(payload, dict):
                                    active = payload.get("channels_active")
                                    total = payload.get("channels_total")
                                    active_list = payload.get("active_channels")
                                    active_more = int(payload.get("active_channels_more") or 0)
                                    inactive_list = payload.get("inactive_channels")
                                    inactive_more = int(payload.get("inactive_channels_more") or 0)
                                    active = int(active) if active is not None else None
                                    total = int(total) if total is not None else None
                            except Exception:
                                active = None
                                total = None
                        # Legacy format: channels_active=..;channels_total=..
                        elif "channels_active=" in raw:
                            try:
                                parts = dict(p.split("=", 1) for p in raw.split(";") if "=" in p)
                                active = (
                                    int(parts.get("channels_active"))
                                    if parts.get("channels_active") not in (None, "None")
                                    else None
                                )
                                total = (
                                    int(parts.get("channels_total"))
                                    if parts.get("channels_total") not in (None, "None")
                                    else None
                                )
                            except Exception:
                                active = None
                                total = None
                    if active is not None and total is not None:
                        lines.append(f"Каналы: <b>{active}/{total}</b> активны")
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
                else:
                    lines.append(
                        f"Баланс: <code>{_fmt_amount(balance_data.balance)} {_safe_text(balance_data.currency)}</code>"
                    )
                    if (
                        _normalize_currency(balance_data.currency) == "RUB"
                        and float(balance_data.balance) <= threshold
                    ):
                        lines.append(f"⚠️ Ниже порога: <code>{threshold:.2f} RUB</code>")
                if balance_data.expiration:
                    expiration_dt = to_app_tz(balance_data.expiration)
                    lines.append(f"Оплатить до: <code>{expiration_dt.strftime('%d.%m.%Y %H:%M %Z')}</code>")
                blocks.append("\n".join(lines))
                _record_balance_snapshot(
                    session,
                    service_id=service.id,
                    balance=float(balance_data.balance),
                    currency=str(balance_data.currency),
                    status="OK",
                )
            else:
                service_title = build_service_title(service.service_name, credentials.get("label"))
                blocks.append(
                    "\n".join(
                        [
                            f"❌ <b>{service_title}</b>",
                            f"Причина: <code>{_safe_text(balance_data.error_message)}</code>",
                        ]
                    )
                )
            service.last_check = datetime.utcnow()
        
        await session.commit()
        report = "📊 <b>Статус сервисов</b>\n\n" + "\n\n".join(blocks)
        await message.answer(report, reply_markup=get_main_menu_keyboard())