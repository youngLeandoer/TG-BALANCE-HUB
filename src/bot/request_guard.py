from __future__ import annotations

import asyncio
from collections import defaultdict


_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def get_guard_lock(*, tg_id: int, name: str) -> asyncio.Lock:
    """
    In-memory guard to prevent double-click duplicate runs.

    Keyed by (name, tg_id). This is process-local: fine for one bot process.
    """
    return _locks[f"{name}:{tg_id}"]

