from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from src.core.config import settings
from src.core.logger import setup_logger

logger = setup_logger(__name__)

_lock = asyncio.Lock()


def _repo_root() -> Path:
    # src/services/local_scrapers.py -> repo root
    return Path(__file__).resolve().parents[2]


async def run_local_scrapers_if_enabled(*, reason: str) -> None:
    """
    Runs tools/run_local_scrapers.py (headless) to push balances via internal endpoints.

    Safe to call multiple times: guarded by an in-process lock.
    """
    if not settings.LOCAL_SCRAPERS_ENABLED:
        return
    if not settings.LOCAL_SCRAPERS_BASE_URL.strip():
        logger.warning("Local scrapers enabled but LOCAL_SCRAPERS_BASE_URL is empty (reason=%s)", reason)
        return
    if not settings.INTERNAL_UPDATE_TOKEN.strip():
        logger.warning("Local scrapers enabled but INTERNAL_UPDATE_TOKEN is empty (reason=%s)", reason)
        return

    async with _lock:
        script = _repo_root() / "tools" / "run_local_scrapers.py"
        if not script.exists():
            logger.warning("Local scrapers script not found at %s (reason=%s)", script, reason)
            return

        cmd: list[str] = [
            sys.executable,
            str(script),
            "--headless",
            "--base-url",
            settings.LOCAL_SCRAPERS_BASE_URL.strip(),
            "--internal-token",
            settings.INTERNAL_UPDATE_TOKEN.strip(),
        ]
        if settings.LOCAL_SCRAPERS_ONLY.strip():
            cmd += ["--only", settings.LOCAL_SCRAPERS_ONLY.strip()]

        logger.info("Running local scrapers (reason=%s): %s", reason, " ".join(cmd))
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=os.environ.copy(),
        )
        try:
            out, _ = await proc.communicate()
        except asyncio.CancelledError:
            # If user cancels /status, try to stop the scraper subprocess too.
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except Exception:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            raise

        text = (out or b"").decode(errors="replace").strip()
        if proc.returncode != 0:
            logger.warning(
                "Local scrapers failed rc=%s (reason=%s). Output:\n%s",
                proc.returncode,
                reason,
                text[-4000:],
            )
        else:
            logger.info("Local scrapers completed OK (reason=%s). Output:\n%s", reason, text[-4000:])

