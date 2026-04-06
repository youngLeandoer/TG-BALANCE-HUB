"""JSON stats aligned with bot /stats (same DB queries and aggregation)."""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import func, select
from src.core.config import settings
from src.core.stats_parsing import (
    _is_monetary_service,
    _normalize_currency,
    _parse_since,
    _parse_stats_args,
    _service_title,
)
from src.core.workspace import get_or_create_workspace_user, get_or_create_workspace_user_with_services
from src.database.models import BalanceHistory, Service, StatsReportHistory
from src.database.session import async_session_maker


def _raw_args_from_query(service: str | None, period: str | None) -> list[str]:
    if service and period:
        return [service, period]
    if service:
        return [service]
    if period:
        return [period]
    return []


async def compute_stats_json(
    *,
    service: str | None = None,
    period: str | None = None,
) -> dict:
    service_filter, period_arg = _parse_stats_args(_raw_args_from_query(service, period))
    since, period_title, period_code = _parse_since(period_arg)
    wid = settings.shared_workspace_tg_id

    async with async_session_maker() as session:
        user = await get_or_create_workspace_user_with_services(session)
        if not user.services:
            return {
                "workspace_found": True,
                "workspace_tg_id": wid,
                "period": {
                    "code": period_code,
                    "title": period_title,
                    "since_utc": since.isoformat() if since else None,
                },
                "services": [],
                "totals_by_currency": {},
                "has_any_snapshots": False,
                "warning": "no_services_added",
            }

        services = [s for s in user.services if s.is_active and s.service_name != "mango_scraper"]
        if service_filter:
            services = [s for s in services if s.service_name.lower() == service_filter]
            if not services:
                return {
                    "workspace_found": True,
                    "workspace_tg_id": wid,
                    "error": "service_not_found",
                    "service_filter": service_filter,
                    "period": {
                        "code": period_code,
                        "title": period_title,
                        "since_utc": since.isoformat() if since else None,
                    },
                    "services": [],
                    "totals_by_currency": {},
                    "has_any_snapshots": False,
                }

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

    rows_by_service: dict[int, list[BalanceHistory]] = defaultdict(list)
    for row in history_rows:
        rows_by_service[row.service_id].append(row)

    totals_by_currency: dict[str, dict[str, float]] = defaultdict(lambda: {"spend": 0.0, "topup": 0.0})
    totals_per_name: dict[str, int] = defaultdict(int)
    for s in services:
        totals_per_name[s.service_name] += 1
    seen_per_name: dict[str, int] = defaultdict(int)

    out_services: list[dict] = []
    has_any_rows = False

    for svc in services:
        seen_per_name[svc.service_name] += 1
        title = _service_title(
            svc,
            index=seen_per_name[svc.service_name],
            total=totals_per_name[svc.service_name],
        )
        rows = rows_by_service.get(svc.id, [])
        base: dict = {
            "service_id": svc.id,
            "service_name": svc.service_name,
            "title": title,
            "snapshot_count": len(rows),
        }

        if not rows:
            out_services.append(
                {
                    **base,
                    "has_snapshots": False,
                    "note": "no_snapshots_in_period",
                }
            )
            continue

        has_any_rows = True
        last = rows[-1]

        if not _is_monetary_service(svc.service_name):
            if svc.service_name == "wazzup":
                delta = None
                if len(rows) >= 2:
                    delta = int(round(last.balance - rows[0].balance))
                out_services.append(
                    {
                        **base,
                        "kind": "wazzup",
                        "has_snapshots": True,
                        "channels_now": int(last.balance),
                        "delta_channels": delta,
                    }
                )
            elif svc.service_name == "yandex_geocoder":
                cur_raw = (last.currency or "").strip().upper()
                out_services.append(
                    {
                        **base,
                        "kind": "yandex_geocoder",
                        "has_snapshots": True,
                        "currency_raw": cur_raw,
                        "estimate_or_metric": float(last.balance) if cur_raw == "REQ" else None,
                        "delta": float(last.balance) - float(rows[0].balance) if len(rows) >= 2 else None,
                    }
                )
            else:
                out_services.append(
                    {
                        **base,
                        "kind": "metric",
                        "has_snapshots": True,
                        "value": float(last.balance),
                        "currency": last.currency,
                    }
                )
            continue

        spend_by_currency: dict[str, float] = defaultdict(float)
        topup_by_currency: dict[str, float] = defaultdict(float)
        for prev, curr in zip(rows, rows[1:]):
            delta = float(curr.balance) - float(prev.balance)
            currency = _normalize_currency(curr.currency or prev.currency or "UNK")
            if delta < 0:
                spend_by_currency[currency] += abs(delta)
            elif delta > 0:
                topup_by_currency[currency] += delta

        current_currency = _normalize_currency(last.currency)
        block = {
            **base,
            "kind": "monetary",
            "has_snapshots": True,
            "current_balance": float(last.balance),
            "currency": current_currency,
            "needs_more_snapshots": len(rows) < 2,
            "spend_by_currency": dict(spend_by_currency),
            "topup_by_currency": dict(topup_by_currency),
        }

        if len(rows) >= 2:
            for cur in sorted(set(list(spend_by_currency.keys()) + list(topup_by_currency.keys()))):
                spend = spend_by_currency[cur]
                topup = topup_by_currency[cur]
                totals_by_currency[cur]["spend"] += spend
                totals_by_currency[cur]["topup"] += topup

        out_services.append(block)

    totals_clean: dict[str, dict[str, float]] = {}
    for cur, vals in totals_by_currency.items():
        if cur == "UNK":
            continue
        totals_clean[cur] = {"spend": float(vals["spend"]), "topup": float(vals["topup"])}

    return {
        "workspace_found": True,
        "workspace_tg_id": wid,
        "period": {
            "code": period_code,
            "title": period_title,
            "since_utc": since.isoformat() if since else None,
        },
        "services": out_services,
        "totals_by_currency": totals_clean,
        "has_any_snapshots": has_any_rows,
        "warning": None
        if has_any_rows
        else "no_snapshots_collect_status_first",
    }


def _label_from_credentials(credentials: object) -> str | None:
    if isinstance(credentials, dict):
        raw = credentials.get("label")
        return str(raw) if raw else None
    return None


async def compute_status_json() -> dict:
    wid = settings.shared_workspace_tg_id
    async with async_session_maker() as session:
        user = await get_or_create_workspace_user_with_services(session)

        active = [
            s
            for s in user.services
            if s.is_active and s.service_name != "mango_scraper"
        ]
        if not active:
            return {"workspace_found": True, "workspace_tg_id": wid, "services": []}

        service_ids = [s.id for s in active]
        subq = (
            select(
                BalanceHistory.service_id.label("sid"),
                func.max(BalanceHistory.checked_at).label("mx"),
            )
            .where(BalanceHistory.service_id.in_(service_ids))
            .group_by(BalanceHistory.service_id)
        ).subquery()

        bh = BalanceHistory
        snap_result = await session.execute(
            select(bh)
            .join(subq, (bh.service_id == subq.c.sid) & (bh.checked_at == subq.c.mx))
        )
        snaps: dict[int, BalanceHistory] = {}
        for row in snap_result.scalars().all():
            prev = snaps.get(row.service_id)
            if prev is None or row.id > prev.id:
                snaps[row.service_id] = row

    totals_per_name: dict[str, int] = defaultdict(int)
    for s in active:
        totals_per_name[s.service_name] += 1
    seen_per_name: dict[str, int] = defaultdict(int)

    items: list[dict] = []
    for s in active:
        seen_per_name[s.service_name] += 1
        title = _service_title(
            s,
            index=seen_per_name[s.service_name],
            total=totals_per_name[s.service_name],
        )
        creds = s.credentials if isinstance(s.credentials, dict) else {}
        snap = snaps.get(s.id)
        items.append(
            {
                "service_id": s.id,
                "service_name": s.service_name,
                "title": title,
                "label": _label_from_credentials(creds),
                "is_active": s.is_active,
                "last_check_utc": s.last_check.isoformat() if s.last_check else None,
                "latest_snapshot": None
                if not snap
                else {
                    "balance": float(snap.balance),
                    "currency": snap.currency,
                    "status": snap.status,
                    "checked_at_utc": snap.checked_at.isoformat() if snap.checked_at else None,
                },
            }
        )

    return {"workspace_found": True, "workspace_tg_id": wid, "services": items}


async def fetch_balance_history(
    *,
    service_id: int | None,
    limit: int,
    offset: int,
) -> dict:
    limit = max(1, min(limit, 500))
    offset = max(0, offset)

    wid = settings.shared_workspace_tg_id
    async with async_session_maker() as session:
        user = await get_or_create_workspace_user(session)

        if service_id is not None:
            own = await session.execute(
                select(Service.id).where(Service.user_id == user.id, Service.id == service_id)
            )
            if own.scalar_one_or_none() is None:
                return {
                    "workspace_found": True,
                    "workspace_tg_id": wid,
                    "error": "service_not_found",
                    "items": [],
                    "total": 0,
                }

        svc_query = select(Service.id).where(Service.user_id == user.id)
        if service_id is not None:
            svc_query = svc_query.where(Service.id == service_id)
        svc_ids_result = await session.execute(svc_query)
        svc_ids = [row[0] for row in svc_ids_result.all()]
        if not svc_ids:
            return {"workspace_found": True, "workspace_tg_id": wid, "items": [], "total": 0}

        count_q = await session.execute(
            select(func.count()).select_from(BalanceHistory).where(BalanceHistory.service_id.in_(svc_ids))
        )
        total = int(count_q.scalar_one() or 0)

        rows = (
            await session.execute(
                select(BalanceHistory)
                .where(BalanceHistory.service_id.in_(svc_ids))
                .order_by(BalanceHistory.checked_at.desc(), BalanceHistory.id.desc())
                .offset(offset)
                .limit(limit)
            )
        ).scalars().all()

    items = [
        {
            "id": r.id,
            "service_id": r.service_id,
            "balance": float(r.balance),
            "currency": r.currency,
            "status": r.status,
            "checked_at_utc": r.checked_at.isoformat() if r.checked_at else None,
        }
        for r in rows
    ]
    return {
        "workspace_found": True,
        "workspace_tg_id": wid,
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


async def fetch_stats_report_history(
    *,
    limit: int,
    offset: int,
) -> dict:
    limit = max(1, min(limit, 100))
    offset = max(0, offset)

    wid = settings.shared_workspace_tg_id
    async with async_session_maker() as session:
        user = await get_or_create_workspace_user(session)

        count_q = await session.execute(
            select(func.count()).select_from(StatsReportHistory).where(StatsReportHistory.user_id == user.id)
        )
        total = int(count_q.scalar_one() or 0)

        rows = (
            await session.execute(
                select(StatsReportHistory)
                .where(StatsReportHistory.user_id == user.id)
                .order_by(StatsReportHistory.generated_at.desc(), StatsReportHistory.id.desc())
                .offset(offset)
                .limit(limit)
            )
        ).scalars().all()

    items = [
        {
            "id": r.id,
            "period_code": r.period_code,
            "service_filter": r.service_filter,
            "since_utc": r.since_at.isoformat() if r.since_at else None,
            "report_text": r.report_text,
            "generated_at_utc": r.generated_at.isoformat() if r.generated_at else None,
        }
        for r in rows
    ]
    return {
        "workspace_found": True,
        "workspace_tg_id": wid,
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }
