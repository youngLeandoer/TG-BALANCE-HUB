from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.deps import require_api_token
from src.api.stats_aggregate import (
    compute_stats_json,
    compute_status_json,
    fetch_balance_history,
    fetch_stats_report_history,
)

router = APIRouter(prefix="/api/v1", tags=["bot-data"])


@router.get("/status")
async def api_status(_: None = Depends(require_api_token)):
    data = await compute_status_json()
    if not data.get("workspace_found"):
        raise HTTPException(status_code=404, detail="Workspace not configured")
    return data


@router.get("/stats")
async def api_stats(
    service: str | None = Query(default=None, description="Filter by service_name, e.g. umnico"),
    period: str | None = Query(
        default=None,
        description="e.g. month, all, 30, or omit for last 3 days (same as /stats)",
    ),
    _: None = Depends(require_api_token),
):
    data = await compute_stats_json(service=service, period=period)
    if not data.get("workspace_found"):
        raise HTTPException(status_code=404, detail="Workspace not configured")
    if data.get("error") == "service_not_found":
        raise HTTPException(status_code=404, detail="Service not found")
    return data


@router.get("/balance-history")
async def api_balance_history(
    service_id: int | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _: None = Depends(require_api_token),
):
    data = await fetch_balance_history(service_id=service_id, limit=limit, offset=offset)
    if not data.get("workspace_found"):
        raise HTTPException(status_code=404, detail="Workspace not configured")
    if data.get("error") == "service_not_found":
        raise HTTPException(status_code=404, detail="Service not found")
    return data


@router.get("/stats-reports")
async def api_stats_reports(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _: None = Depends(require_api_token),
):
    data = await fetch_stats_report_history(limit=limit, offset=offset)
    if not data.get("workspace_found"):
        raise HTTPException(status_code=404, detail="Workspace not configured")
    return data
