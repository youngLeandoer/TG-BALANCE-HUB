from __future__ import annotations

import asyncio
from collections import defaultdict


_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
_tasks: dict[int, asyncio.Task] = {}


def get_guard_lock(*, tg_id: int, name: str) -> asyncio.Lock:
    """
    In-memory guard to prevent double-click duplicate runs.

    Keyed by (name, tg_id). This is process-local: fine for one bot process.
    """
    return _locks[f"{name}:{tg_id}"]


def set_current_task(*, tg_id: int, task: asyncio.Task) -> None:
    _tasks[tg_id] = task


def clear_current_task(*, tg_id: int, task: asyncio.Task | None = None) -> None:
    current = _tasks.get(tg_id)
    if current is None:
        return
    if task is None or task is current:
        _tasks.pop(tg_id, None)


def cancel_current_task(*, tg_id: int) -> bool:
    task = _tasks.get(tg_id)
    if task is None or task.done():
        _tasks.pop(tg_id, None)
        return False
    task.cancel()
    return True


def has_running_task(*, tg_id: int) -> bool:
    task = _tasks.get(tg_id)
    if task is None:
        return False
    return not task.done()

