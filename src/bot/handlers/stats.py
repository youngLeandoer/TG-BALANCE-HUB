from __future__ import annotations

import asyncio
from collections import defaultdict
import csv
from datetime import datetime, timedelta
import html
import io
import json

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.types import BufferedInputFile
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.core.logger import setup_logger
from src.core.report_formatting import SERVICE_MESSAGE_DELAY_SEC
from src.bot.keyboards.main_menu import get_main_menu_keyboard
from src.database.models import BalanceHistory, Service, StatsReportHistory, User
from src.database.session import async_session_maker

logger = setup_logger(__name__)
router = Router()

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


def _safe_text(value: object) -> str:
    return html.escape(str(value), quote=False)


def _fmt_money(amount: float) -> str:
    try:
        return f"{float(amount):,.2f}".replace(",", " ")
    except Exception:
        return _safe_text(amount)


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


def _period_filename_tag(period_code: str) -> str:
    return {
        "3d": "3days",
        "month": "month",
        "all": "alltime",
    }.get(period_code, period_code)


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


async def _render_stats(message: types.Message, raw_args: list[str]):
    tg_id = message.from_user.id
    service_filter, period_arg = _parse_stats_args(raw_args)

    since, period_title, period_code = _parse_since(period_arg)
    async with async_session_maker() as session:
        user_result = await session.execute(
            select(User).where(User.tg_id == tg_id).options(selectinload(User.services))
        )
        user = user_result.scalar_one_or_none()
        if not user or not user.services:
            await message.answer(
                "📉 <b>Статистика</b>\n\nНет добавленных сервисов.",
                reply_markup=get_main_menu_keyboard(),
            )
            return

        services = [s for s in user.services if s.is_active and s.service_name != "mango_scraper"]
        if service_filter:
            services = [s for s in services if s.service_name.lower() == service_filter]
            if not services:
                await message.answer(
                    "📉 <b>Статистика</b>\n\n"
                    f"Сервис <code>{service_filter}</code> не найден среди активных.",
                    reply_markup=get_main_menu_keyboard(),
                )
                return

        service_ids = [s.id for s in services]
        query = (
            select(BalanceHistory)
            .where(
                BalanceHistory.service_id.in_(service_ids),
                BalanceHistory.status == "OK",
            )
            .order_by(BalanceHistory.service_id.asc(), BalanceHistory.checked_at.asc())
        )
        if since is not None:
            query = query.where(BalanceHistory.checked_at >= since)
        history_result = await session.execute(query)
        history_rows = history_result.scalars().all()

    rows_by_service: dict[int, list[BalanceHistory]] = defaultdict(list)
    for row in history_rows:
        rows_by_service[row.service_id].append(row)

    totals_by_currency = defaultdict(lambda: {"spend": 0.0, "topup": 0.0})
    messages: list[str] = [
        "📉 <b>Финансовая статистика</b>\n\n" f"Период: <code>{_safe_text(period_title)}</code>",
    ]
    totals_per_name = defaultdict(int)
    for s in services:
        totals_per_name[s.service_name] += 1
    seen_per_name = defaultdict(int)

    has_any_rows = False
    for service in services:
        seen_per_name[service.service_name] += 1
        title = _service_title(
            service,
            index=seen_per_name[service.service_name],
            total=totals_per_name[service.service_name],
        )
        title_safe = _safe_text(title)
        rows = rows_by_service.get(service.id, [])
        if not rows:
            messages.append(
                "\n".join(
                    [
                        f"⏳ <b>{title_safe}</b>",
                        "Нет замеров за выбранный период (нажмите «Статус», чтобы появилась история).",
                    ]
                )
            )
            continue

        has_any_rows = True
        last = rows[-1]

        if not _is_monetary_service(service.service_name):
            if service.service_name == "wazzup":
                lines = [
                    f"✅ <b>{title_safe}</b>",
                    "Метрика: <code>активные каналы</code>",
                    f"Сейчас: <b>{int(last.balance)}</b>",
                ]
                if len(rows) >= 2:
                    diff = int(round(last.balance - rows[0].balance))
                    lines.append(f"Изменение за период: <code>{diff:+d}</code>")
                else:
                    lines.append("Изменение за период: <code>нужен ещё один замер</code>")
                messages.append("\n".join(lines))
            elif service.service_name == "yandex_geocoder":
                cur_raw = (last.currency or "").strip().upper()
                lines = [f"✅ <b>{title_safe}</b>", "Метрика: <code>геокодер (не деньги)</code>"]
                if cur_raw == "REQ":
                    lines.append(f"Оценка остатка лимита: <code>{_fmt_money(float(last.balance))}</code>")
                else:
                    lines.append("Проверка API: <code>доступен</code>")
                if len(rows) >= 2:
                    diff = float(last.balance) - float(rows[0].balance)
                    lines.append(f"Изменение за период: <code>{diff:+.2f}</code>")
                messages.append("\n".join(lines))
            else:
                messages.append(
                    "\n".join(
                        [
                            f"✅ <b>{title_safe}</b>",
                            f"Значение: <code>{_fmt_money(float(last.balance))}</code>",
                        ]
                    )
                )
            continue

        spend_by_currency = defaultdict(float)
        topup_by_currency = defaultdict(float)
        for prev, curr in zip(rows, rows[1:]):
            delta = float(curr.balance) - float(prev.balance)
            currency = _normalize_currency(curr.currency or prev.currency or "UNK")
            if delta < 0:
                spend_by_currency[currency] += abs(delta)
            elif delta > 0:
                topup_by_currency[currency] += delta

        current_currency = _normalize_currency(last.currency)
        lines = [
            f"✅ <b>{title_safe}</b>",
            f"Текущий баланс: <code>{_fmt_money(float(last.balance))} {_safe_text(current_currency)}</code>",
        ]
        if len(rows) < 2:
            lines.append("Движение за период: <code>нужно минимум 2 замера</code> (нажмите «Статус» ещё раз позже).")
            messages.append("\n".join(lines))
            continue

        currencies = sorted(set(list(spend_by_currency.keys()) + list(topup_by_currency.keys())))
        if not currencies:
            currencies = [current_currency]
        lines.append("<b>За период</b>:")
        for cur in currencies:
            spend = spend_by_currency[cur]
            topup = topup_by_currency[cur]
            totals_by_currency[cur]["spend"] += spend
            totals_by_currency[cur]["topup"] += topup
            lines.append(
                f"• {_safe_text(cur)} — расход <code>{_fmt_money(spend)}</code>, "
                f"пополнения <code>{_fmt_money(topup)}</code>"
            )
        messages.append("\n".join(lines))

    if not has_any_rows:
        messages.append(
            "⚠️ Нет ни одного замера за период — сначала соберите историю через «Статус»."
        )
    elif totals_by_currency:
        total_lines = ["📊 <b>Итого по валютам</b>"]
        shown_totals = 0
        for cur in sorted(totals_by_currency.keys()):
            if cur == "UNK":
                continue
            spend = totals_by_currency[cur]["spend"]
            topup = totals_by_currency[cur]["topup"]
            total_lines.append(f"✅ <b>{_safe_text(cur)}</b>")
            total_lines.append(f"Расход: <code>{_fmt_money(spend)}</code>")
            total_lines.append(f"Пополнения: <code>{_fmt_money(topup)}</code>")
            shown_totals += 1
        if shown_totals == 0:
            total_lines.append("Нет агрегированных сумм по валютам (проверьте валюты в замерах).")
        messages.append("\n".join(total_lines))

    messages.append(
        "<i>Команды:</i> <code>/stats</code>, <code>/stats month</code>, <code>/stats all</code>, "
        "<code>/stats_export</code>"
    )
    report = "\n\n---\n\n".join(messages)

    # Аудит: сохраняем сгенерированный отчёт в БД.
    async with async_session_maker() as session:
        user_result = await session.execute(select(User).where(User.tg_id == tg_id))
        user = user_result.scalar_one_or_none()
        if user:
            session.add(
                StatsReportHistory(
                    user_id=user.id,
                    period_code=period_code,
                    service_filter=service_filter,
                    since_at=since,
                    report_text=report,
                    generated_at=datetime.utcnow(),
                )
            )
            await session.commit()
    n = len(messages)
    for i, chunk in enumerate(messages):
        if i > 0:
            await asyncio.sleep(SERVICE_MESSAGE_DELAY_SEC)
        await message.answer(
            chunk,
            reply_markup=get_main_menu_keyboard() if i == n - 1 else None,
        )


async def _send_stats_export_csv(message: types.Message, raw_args: list[str]) -> None:
    tg_id = message.from_user.id
    service_filter, period_arg = _parse_stats_args(raw_args)
    since, period_title, period_code = _parse_since(period_arg)

    async with async_session_maker() as session:
        user_result = await session.execute(
            select(User).where(User.tg_id == tg_id).options(selectinload(User.services))
        )
        user = user_result.scalar_one_or_none()
        if not user or not user.services:
            await message.answer(
                "📤 <b>Экспорт</b>\n\nНет добавленных сервисов.",
                reply_markup=get_main_menu_keyboard(),
            )
            return

        services = [s for s in user.services if s.is_active and s.service_name != "mango_scraper"]
        if service_filter:
            services = [s for s in services if s.service_name.lower() == service_filter]
            if not services:
                await message.answer(
                    "📤 <b>Экспорт</b>\n\n"
                    f"Сервис <code>{service_filter}</code> не найден среди активных.",
                    reply_markup=get_main_menu_keyboard(),
                )
                return

        service_ids = [s.id for s in services]
        query = (
            select(BalanceHistory)
            .where(
                BalanceHistory.service_id.in_(service_ids),
                BalanceHistory.status == "OK",
            )
            .order_by(BalanceHistory.service_id.asc(), BalanceHistory.checked_at.asc())
        )
        if since is not None:
            query = query.where(BalanceHistory.checked_at >= since)
        history_rows = (await session.execute(query)).scalars().all()

    if not history_rows:
        await message.answer(
            f"📤 <b>Экспорт</b>\n\nНет данных за период: {period_title}.",
            reply_markup=get_main_menu_keyboard(),
        )
        return

    totals_per_name = defaultdict(int)
    for s in services:
        totals_per_name[s.service_name] += 1
    seen_per_name = defaultdict(int)
    title_by_service_id: dict[int, str] = {}
    for s in services:
        seen_per_name[s.service_name] += 1
        title_by_service_id[s.id] = _service_title(
            s,
            index=seen_per_name[s.service_name],
            total=totals_per_name[s.service_name],
        )

    rows_by_service: dict[int, list[BalanceHistory]] = defaultdict(list)
    for row in history_rows:
        rows_by_service[row.service_id].append(row)

    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(
        [
            "Сервис",
            "Код_сервиса",
            "Тип",
            "Период",
            "С_UTC",
            "По_UTC",
            "Кол_во_замеров",
            "Валюта_или_единица",
            "Значение_в_начале",
            "Значение_в_конце",
            "Изменение",
            "Расход",
            "Пополнения",
        ]
    )

    totals_by_currency = defaultdict(lambda: {"spend": 0.0, "topup": 0.0})
    for service in services:
        rows = rows_by_service.get(service.id, [])
        service_title = title_by_service_id.get(service.id, service.service_name)
        is_money = _is_monetary_service(service.service_name)

        if not rows:
            writer.writerow(
                [
                    service_title,
                    service.service_name,
                    "money" if is_money else "metric",
                    period_title,
                    "",
                    "",
                    0,
                    "RUB" if is_money else "channels",
                    "",
                    "",
                    "",
                    "",
                    "",
                ]
            )
            continue

        start = float(rows[0].balance)
        end = float(rows[-1].balance)
        change = end - start
        from_utc = rows[0].checked_at.isoformat()
        to_utc = rows[-1].checked_at.isoformat()

        if is_money:
            spend = 0.0
            topup = 0.0
            for prev, curr in zip(rows, rows[1:]):
                delta = float(curr.balance) - float(prev.balance)
                if delta < 0:
                    spend += abs(delta)
                elif delta > 0:
                    topup += delta
            currency = _normalize_currency(rows[-1].currency or rows[0].currency or "RUB")
            totals_by_currency[currency]["spend"] += spend
            totals_by_currency[currency]["topup"] += topup
            writer.writerow(
                [
                    service_title,
                    service.service_name,
                    "money",
                    period_title,
                    from_utc,
                    to_utc,
                    len(rows),
                    currency,
                    f"{start:.2f}",
                    f"{end:.2f}",
                    f"{change:+.2f}",
                    f"{spend:.2f}",
                    f"{topup:.2f}",
                ]
            )
        else:
            writer.writerow(
                [
                    service_title,
                    service.service_name,
                    "metric",
                    period_title,
                    from_utc,
                    to_utc,
                    len(rows),
                    "channels",
                    f"{start:.0f}",
                    f"{end:.0f}",
                    f"{change:+.0f}",
                    "",
                    "",
                ]
            )

    writer.writerow([])
    writer.writerow(["ИТОГИ_ПО_ВАЛЮТАМ"])
    writer.writerow(["Валюта", "Расход", "Пополнения"])
    for cur in sorted(totals_by_currency.keys()):
        writer.writerow(
            [
                cur,
                f"{totals_by_currency[cur]['spend']:.2f}",
                f"{totals_by_currency[cur]['topup']:.2f}",
            ]
        )

    content = output.getvalue()
    output.close()

    filename = f"stats_{_period_filename_tag(period_code)}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    file = BufferedInputFile(content.encode("utf-8-sig"), filename=filename)

    # Храним факт генерации экспорта.
    async with async_session_maker() as session:
        user_result = await session.execute(select(User).where(User.tg_id == tg_id))
        user = user_result.scalar_one_or_none()
        if user:
            session.add(
                StatsReportHistory(
                    user_id=user.id,
                    period_code=f"csv:{period_code}",
                    service_filter=service_filter,
                    since_at=since,
                    report_text=f"CSV export: {filename} ({period_title})",
                    generated_at=datetime.utcnow(),
                )
            )
            await session.commit()

    await message.answer_document(
        file,
        caption=f"📤 Экспорт статистики ({period_title})",
        reply_markup=get_main_menu_keyboard(),
    )


@router.message(Command("stats"))
async def cmd_stats(message: types.Message):
    raw_args = message.text.split(maxsplit=2)[1:] if message.text else []
    await _render_stats(message, raw_args)


@router.message(F.text == "📉 Статистика (3 дня)")
async def cmd_stats_30_days(message: types.Message):
    await _render_stats(message, [])


@router.message(F.text == "📉 Статистика (30 дней)")
async def cmd_stats_30_days_legacy_button(message: types.Message):
    await _render_stats(message, ["30"])


@router.message(F.text == "📅 Статистика (месяц)")
async def cmd_stats_month(message: types.Message):
    await _render_stats(message, ["month"])


@router.message(F.text == "🗂 Статистика (всё время)")
async def cmd_stats_all(message: types.Message):
    await _render_stats(message, ["all"])


@router.message(Command("stats_export"))
async def cmd_stats_export(message: types.Message):
    raw_args = message.text.split(maxsplit=2)[1:] if message.text else []
    await _send_stats_export_csv(message, raw_args)


@router.message(F.text == "📤 Экспорт CSV (3 дня)")
async def cmd_stats_export_3d(message: types.Message):
    await _send_stats_export_csv(message, ["3"])


@router.message(F.text == "📤 Экспорт CSV (месяц)")
async def cmd_stats_export_month(message: types.Message):
    await _send_stats_export_csv(message, ["month"])


@router.message(F.text == "📤 Экспорт CSV (всё время)")
async def cmd_stats_export_all(message: types.Message):
    await _send_stats_export_csv(message, ["all"])

