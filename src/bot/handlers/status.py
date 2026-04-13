from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command
import asyncio
from datetime import datetime
import json
import html
from dataclasses import dataclass
from typing import Any
from sqlalchemy import select
from src.database.models import Service, BalanceHistory
from src.services.base import connector_wait_timeout_seconds
from src.database.session import async_session_maker
from src.core.workspace import get_or_create_workspace_user_with_services
from src.core.config import settings
from src.core.logger import setup_logger
from src.core.timezone import format_app_dt, parse_iso_as_utc, to_app_tz
from src.core.report_formatting import SERVICE_MESSAGE_DELAY_SEC
from src.bot.keyboards.main_menu import get_main_menu_keyboard
from src.services.providers.umnico import UmnicoConnector
from src.services.providers.hosterby import HosterByConnector
from src.services.providers.smsaero import SMSAeroConnector
from src.services.providers.avito import AvitoConnector
from src.services.providers.regru import RegRuConnector
from src.services.providers.wazzup import WazzupConnector
from src.services.providers.timewebcloud import TimewebCloudConnector
from src.services.providers.selectel import SelectelConnector
from src.services.local_scrapers import run_local_scrapers_if_enabled
from src.services.providers.yandex_cloud import YandexCloudConnector
from src.services.providers.yandex_geocoder import YandexGeocoderConnector

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
    "yandex_cloud": YandexCloudConnector,
    "yandex_geocoder": YandexGeocoderConnector,
}

STATUS_MAX_CONCURRENCY = 6


@dataclass(slots=True)
class _StatusResult:
    block: str
    should_set_last_check: bool = False
    snapshot: tuple[float, str] | None = None  # (balance, currency)


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
        user = await get_or_create_workspace_user_with_services(session)

        if not user.services:
            await message.answer(
                "📊 <b>Статус сервисов</b>\n\n"
                "У вас пока нет добавленных сервисов.\n"
                "Используйте /add для добавления",
                reply_markup=get_main_menu_keyboard(),
            )
            return

        active_services: list[Service] = []
        credentials_by_service_id: dict[int, dict[str, Any]] = {}

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

        title_by_service_id: dict[int, str] = {}
        scraper_service_ids: list[int] = []
        scraper_services: list[Service] = []
        api_services: list[Service] = []

        for service in user.services:
            if not service.is_active or service.service_name == "mango_scraper":
                continue

            try:
                credentials: Any = service.credentials
                if isinstance(credentials, str):
                    credentials = json.loads(credentials)
                if not isinstance(credentials, dict):
                    raise ValueError("credentials must be dict")
            except Exception as parse_error:
                title_by_service_id[service.id] = build_service_title(service.service_name)
                credentials_by_service_id[service.id] = {"__parse_error__": parse_error}
                active_services.append(service)
                continue

            credentials_by_service_id[service.id] = credentials

            if service.service_name in {"adminvps_scraper", "atlex_scraper", "nic_scraper"}:
                base_title = (
                    "AdminVPS"
                    if service.service_name == "adminvps_scraper"
                    else ("ATLEX" if service.service_name == "atlex_scraper" else "NIC.RU")
                )
                title_by_service_id[service.id] = build_service_title(
                    service.service_name, credentials.get("label"), base_title=base_title
                )
                scraper_service_ids.append(service.id)
                scraper_services.append(service)
            else:
                title_by_service_id[service.id] = build_service_title(service.service_name, credentials.get("label"))
                api_services.append(service)

            active_services.append(service)

        # Kick off local scrapers in background (do NOT block /status).
        scraper_task: asyncio.Task | None = None
        if scraper_services:
            scraper_task = asyncio.create_task(run_local_scrapers_if_enabled(reason="status_command"))

        sem = asyncio.Semaphore(STATUS_MAX_CONCURRENCY)

        async def _fetch_one(service: Service) -> _StatusResult:
                title = title_by_service_id.get(service.id, service.service_name.title())
                credentials = credentials_by_service_id.get(service.id, {}) or {}

                parse_error = credentials.get("__parse_error__")
                if parse_error:
                    return _StatusResult(
                        block="\n".join(
                            [
                                f"❌ <b>{title}</b>",
                                f"Причина: <code>{_safe_text(parse_error)}</code>",
                            ]
                        )
                    )

                if service.service_name in {"adminvps_scraper", "atlex_scraper", "nic_scraper"}:
                    if "manual_balance" in credentials:
                        manual_balance = float(credentials.get("manual_balance", 0.0))
                        manual_currency = str(credentials.get("manual_currency", "RUB"))
                        threshold = _resolve_alert_threshold_rub(credentials)
                        lines = [
                            f"✅ <b>{title}</b>",
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
                        return _StatusResult(
                            block="\n".join(lines),
                            should_set_last_check=True,
                            snapshot=(manual_balance, manual_currency),
                        )

                    if settings.LOCAL_SCRAPERS_ENABLED:
                        return _StatusResult(
                            block="\n".join(
                                [
                                    f"⏳ <b>{title}</b>",
                                    "Запрашиваю баланс через локальный скрейпер (может занять 1–3 минуты).",
                                    "Как только получу — пришлю обновление ниже.",
                                ]
                            )
                        )

                    helper = (
                        "tools/adminvps_local_browser.py"
                        if service.service_name == "adminvps_scraper"
                        else (
                            "tools/atlex_local_browser.py"
                            if service.service_name == "atlex_scraper"
                            else "tools/nic_local_browser.py"
                        )
                    )
                    env_prefix = (
                        "ADMINVPS_LOCAL_*"
                        if service.service_name == "adminvps_scraper"
                        else ("ATLEX_LOCAL_*" if service.service_name == "atlex_scraper" else "NIC_LOCAL_*")
                    )
                    return _StatusResult(
                        block="\n".join(
                            [
                                f"⏳ <b>{title}</b>",
                                "Ожидаю баланс с вашего ПК.",
                                f"Запуск: <code>{helper}</code>",
                                f"Переменные: <code>{env_prefix}</code> + <code>INTERNAL_UPDATE_TOKEN</code>",
                            ]
                        )
                    )

                connector_cls = SERVICE_CONNECTORS.get(service.service_name)
                if not connector_cls:
                    return _StatusResult(
                        block="\n".join(
                            [
                                f"❌ <b>{title}</b>",
                                "Причина: <code>сервис пока не поддерживается</code>",
                            ]
                        )
                    )

                async with sem:
                    try:
                        connector = connector_cls(credentials=credentials)
                        balance_data = await asyncio.wait_for(
                            connector.get_balance_data(),
                            timeout=connector_wait_timeout_seconds(service.service_name),
                        )
                    except asyncio.TimeoutError:
                        return _StatusResult(
                            block="\n".join(
                                [
                                    f"❌ <b>{title}</b>",
                                    "Причина: <code>таймаут запроса к API</code>",
                                ]
                            )
                        )
                    except Exception as exc:
                        logger.warning(
                            "Service check failed service_id=%s name=%s err=%s",
                            service.id,
                            service.service_name,
                            exc,
                        )
                        return _StatusResult(
                            block="\n".join(
                                [
                                    f"❌ <b>{title}</b>",
                                    "Причина: <code>ошибка запроса к API</code>",
                                ]
                            )
                        )

                if balance_data.status == "OK":
                    threshold = _resolve_alert_threshold_rub(credentials)
                    lines = [f"✅ <b>{title}</b>"]
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
                        if _normalize_currency(balance_data.currency) == "RUB" and float(balance_data.balance) <= threshold:
                            lines.append(f"⚠️ Ниже порога: <code>{threshold:.2f} RUB</code>")

                    if balance_data.expiration:
                        expiration_dt = to_app_tz(balance_data.expiration)
                        lines.append(f"Оплачено до: <code>{expiration_dt.strftime('%d.%m.%Y %H:%M %Z')}</code>")

                    return _StatusResult(
                        block="\n".join(lines),
                        should_set_last_check=True,
                        snapshot=(float(balance_data.balance), str(balance_data.currency)),
                    )

                return _StatusResult(
                    block="\n".join(
                        [
                            f"❌ <b>{title}</b>",
                            f"Причина: <code>{_safe_text(balance_data.error_message)}</code>",
                        ]
                    ),
                    should_set_last_check=True,
                )

        # Fetch API services concurrently, but keep output snappy:
        # - API services: immediate results
        # - Scraper services: show "running" placeholders, then send follow-up after scrapers finish
        ordered_services = [*api_services]
        results: list[_StatusResult] = await asyncio.gather(*[_fetch_one(svc) for svc in ordered_services])

        blocks: list[str] = []
        for service, res in zip(ordered_services, results):
            blocks.append(res.block)
            if res.snapshot is not None:
                bal, cur = res.snapshot
                _record_balance_snapshot(
                    session,
                    service_id=service.id,
                    balance=float(bal),
                    currency=str(cur),
                    status="OK",
                )
            if res.should_set_last_check:
                service.last_check = datetime.utcnow()

        await session.commit()

    if not blocks:
        await message.answer(
            "📊 <b>Статус сервисов</b>\n\n"
            "Нет активных сервисов для проверки.",
            reply_markup=get_main_menu_keyboard(),
        )
        return

    await message.answer("📊 <b>Статус сервисов</b>")
    n = len(blocks)
    for i, block in enumerate(blocks):
        if i > 0:
            await asyncio.sleep(SERVICE_MESSAGE_DELAY_SEC)
        await message.answer(
            block,
            reply_markup=get_main_menu_keyboard() if i == n - 1 else None,
        )

    # Follow-up: once scrapers are done, send updated blocks for them only.
    if scraper_task and settings.LOCAL_SCRAPERS_ENABLED:
        await message.answer("🔁 <b>Обновление по скрейперам, ожидайте(нужно как-то оптимизировать)</b>")
        try:
            await asyncio.wait_for(scraper_task, timeout=180.0)
        except asyncio.TimeoutError:
            await message.answer(
                "⏳ <b>Скрейперы</b>\n\n"
                "Пока не успели завершиться (таймаут ожидания). "
                "Попробуйте ещё раз чуть позже.",
                reply_markup=get_main_menu_keyboard(),
            )
            return
        except Exception as exc:
            logger.warning("Local scrapers failed for /status (tg_id=%s): %s", tg_id, exc)
            await message.answer(
                "❌ <b>Скрейперы</b>\n\n"
                "Не удалось получить данные со скрейперов. "
                "Проверьте логи и попробуйте позже.",
                reply_markup=get_main_menu_keyboard(),
            )
            return

        async with async_session_maker() as session:
            user2 = await get_or_create_workspace_user_with_services(session)
            if not user2.services:
                return
            svc_by_id = {s.id: s for s in user2.services}

            # rebuild titles with existing totals (stable output)
            service_totals2: dict[str, int] = {}
            for svc in user2.services:
                if not svc.is_active or svc.service_name == "mango_scraper":
                    continue
                service_totals2[svc.service_name] = service_totals2.get(svc.service_name, 0) + 1
            service_seen2: dict[str, int] = {}

            def build_title2(service: Service, cred: dict[str, Any]) -> str:
                base_title = None
                if service.service_name == "adminvps_scraper":
                    base_title = "AdminVPS"
                elif service.service_name == "atlex_scraper":
                    base_title = "ATLEX"
                elif service.service_name == "nic_scraper":
                    base_title = "NIC.RU"
                title = base_title or service.service_name.title()
                label = cred.get("label") if isinstance(cred, dict) else None
                if label:
                    return f"{title} ({label})"
                total = service_totals2.get(service.service_name, 1)
                if total > 1:
                    current = service_seen2.get(service.service_name, 0) + 1
                    service_seen2[service.service_name] = current
                    return f"{title} (аккаунт {current}/{total})"
                return title

            updated_blocks: list[str] = []
            for sid in scraper_service_ids:
                svc = svc_by_id.get(sid)
                if not svc or not svc.is_active:
                    continue
                cred: Any = svc.credentials or {}
                if isinstance(cred, str):
                    try:
                        cred = json.loads(cred)
                    except Exception:
                        cred = {}
                if not isinstance(cred, dict):
                    cred = {}

                title = build_title2(svc, cred)
                if "manual_balance" not in cred:
                    updated_blocks.append(
                        "\n".join(
                            [
                                f"❌ <b>{title}</b>",
                                "Причина: <code>скрейпер не прислал баланс</code>",
                            ]
                        )
                    )
                    continue

                manual_balance = float(cred.get("manual_balance", 0.0))
                manual_currency = str(cred.get("manual_currency", "RUB"))
                threshold = _resolve_alert_threshold_rub(cred)
                lines = [
                    f"✅ <b>{title}</b>",
                    f"Баланс: <code>{_fmt_amount(manual_balance)} {_safe_text(manual_currency)}</code>",
                ]
                if _normalize_currency(manual_currency) == "RUB" and manual_balance <= threshold:
                    lines.append(f"⚠️ Ниже порога: <code>{threshold:.2f} RUB</code>")
                manual_updated_at = cred.get("manual_updated_at")
                if isinstance(manual_updated_at, str) and manual_updated_at:
                    parsed = parse_iso_as_utc(manual_updated_at)
                    lines.append(
                        f"Обновлено: <code>{_safe_text(format_app_dt(parsed) if parsed else manual_updated_at)}</code>"
                    )
                updated_blocks.append("\n".join(lines))

            if updated_blocks:
                m = len(updated_blocks)
                for i, block in enumerate(updated_blocks):
                    if i > 0:
                        await asyncio.sleep(SERVICE_MESSAGE_DELAY_SEC)
                    await message.answer(
                        block,
                        reply_markup=get_main_menu_keyboard() if i == m - 1 else None,
                    )