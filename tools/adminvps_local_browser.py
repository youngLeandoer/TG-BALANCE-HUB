#!/usr/bin/env python3
"""
Local AdminVPS browser helper.

1) Opens browser (Playwright, headed)
2) Logs into AdminVPS (autofill or manual)
3) Parses balance from page HTML
4) POST balance to Balance Hub: /internal/adminvps/balance

Example:
  python tools/adminvps_local_browser.py
(env: ADMINVPS_LOCAL_TG_ID, ADMINVPS_LOCAL_LABEL, INTERNAL_UPDATE_TOKEN,
 ADMINVPS_LOCAL_BASE_URL, ADMINVPS_LOCAL_LOGIN, ADMINVPS_LOCAL_PASSWORD)
"""

import argparse
import json
import os
import re
import sys
import time
from typing import Optional

import httpx
from dotenv import load_dotenv

BALANCE_PATTERNS = (
    r"Баланс[^0-9\-]*([-+]?\d[\d\s]*[.,]\d{1,2})",
    r"\"balance\"\\s*:\\s*\"?([-+]?\d[\d\s]*[.,]?\d*)\"?",
    r"data-balance=\"([-+]?\d[\d\s]*[.,]?\d*)\"",
)


def parse_balance(text: str) -> float:
    for pattern in BALANCE_PATTERNS:
        m = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if m:
            raw = m.group(1).replace(" ", "").replace(",", ".")
            return float(raw)
    raise RuntimeError("Balance not found on page")


def safe_goto(page, url: str, timeout: int = 30000, attempts: int = 3) -> None:
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            return
        except Exception as exc:
            last_exc = exc
            if "ERR_NETWORK_CHANGED" not in str(exc) or attempt == attempts:
                raise
            time.sleep(1.5)
    if last_exc:
        raise last_exc


def run_browser(login: Optional[str], password: Optional[str], dashboard_url: str, wait_login_seconds: int) -> float:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. Run: pip install playwright && playwright install chromium"
        ) from exc

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=bool(os.getenv("PLAYWRIGHT_HEADLESS", "") == "1"))
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        safe_goto(page, dashboard_url, timeout=30000, attempts=3)

        if login and password:
            for selector in ("input[name='login']", "input[type='email']", "#login"):
                if page.locator(selector).count() > 0:
                    page.fill(selector, login)
                    break
            for selector in ("input[name='password']", "input[type='password']", "#password"):
                if page.locator(selector).count() > 0:
                    page.fill(selector, password)
                    break
            for selector in ("button[type='submit']", "button:has-text('Войти')", "button:has-text('Login')"):
                if page.locator(selector).count() > 0:
                    page.click(selector)
                    break

        # Avoid fixed sleeps; wait briefly for login/redirect, but allow manual steps.
        deadline = time.time() + max(int(wait_login_seconds), 5)
        while time.time() < deadline:
            page.wait_for_timeout(600)
            try:
                content = page.content()
            except Exception:
                content = ""
            # Heuristic: if we are not on login form anymore, proceed.
            if ("password" not in content.lower()) and ("войти" not in content.lower()):
                break

        safe_goto(page, dashboard_url, timeout=30000, attempts=3)
        content = page.content()
        browser.close()
        return parse_balance(content)


def _push_error_message(status_code: int, url: str, body_text: str) -> str:
    detail = ""
    try:
        data = json.loads(body_text or "{}")
        if isinstance(data, dict) and isinstance(data.get("detail"), str):
            detail = data["detail"]
    except json.JSONDecodeError:
        pass

    if status_code == 401:
        return f"HTTP 401: неверный INTERNAL_UPDATE_TOKEN. Ответ: {body_text[:300]}"
    if status_code == 404 and detail == "User not found":
        return (
            "HTTP 404 «User not found»: в БД нет пользователя с таким tg_id. "
            "ADMINVPS_LOCAL_TG_ID должен совпадать с вашим Telegram ID."
        )
    if status_code == 404 and "with this label not found" in detail:
        return (
            "HTTP 404: нет adminvps_scraper с таким label. "
            "ADMINVPS_LOCAL_LABEL = часть до первого «|» при /add."
        )
    return f"HTTP {status_code} URL={url}. Ответ: {body_text[:400]}"


def push_balance(base_url: str, token: str, tg_id: int, label: str, balance: float, currency: str) -> None:
    payload = {
        "tg_id": tg_id,
        "label": label,
        "balance": balance,
        "currency": currency,
    }
    headers = {"x-internal-token": token}
    url = f"{base_url.rstrip('/')}/internal/adminvps/balance"
    with httpx.Client(timeout=20.0) as client:
        resp = client.post(url, json=payload, headers=headers)
        if resp.is_error:
            raise RuntimeError(_push_error_message(resp.status_code, url, resp.text)) from None


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="AdminVPS local browser balance fetcher")
    parser.add_argument("--tg-id", type=int, default=int(os.getenv("ADMINVPS_LOCAL_TG_ID", "0")))
    parser.add_argument("--label", default=os.getenv("ADMINVPS_LOCAL_LABEL", "Main"))
    parser.add_argument("--internal-token", default=os.getenv("INTERNAL_UPDATE_TOKEN", ""))
    parser.add_argument("--base-url", default=os.getenv("ADMINVPS_LOCAL_BASE_URL", "http://localhost:8000"))
    parser.add_argument("--dashboard-url", default=os.getenv("ADMINVPS_DASHBOARD_URL", "https://my.adminvps.ru/"))
    parser.add_argument("--currency", default=os.getenv("ADMINVPS_LOCAL_CURRENCY", "RUB"))
    parser.add_argument(
        "--wait-login-seconds",
        type=int,
        default=int(os.getenv("ADMINVPS_WAIT_LOGIN_SECONDS", "20")),
        help="Seconds to wait for login/redirect (manual steps/captcha).",
    )
    parser.add_argument("--headless", action="store_true", help="Run browser headless (no X server needed)")
    parser.add_argument("--headed", action="store_true", help="Run browser with UI (requires X server)")
    parser.add_argument("--login", default=os.getenv("ADMINVPS_LOCAL_LOGIN"))
    parser.add_argument("--password", default=os.getenv("ADMINVPS_LOCAL_PASSWORD"))
    args = parser.parse_args()

    if args.headless and args.headed:
        raise RuntimeError("Pass only one: --headless or --headed")
    if args.headless:
        os.environ["PLAYWRIGHT_HEADLESS"] = "1"
    if args.headed:
        os.environ["PLAYWRIGHT_HEADLESS"] = "0"

    if not args.tg_id:
        raise RuntimeError("Set ADMINVPS_LOCAL_TG_ID in .env or pass --tg-id")
    if not args.internal_token:
        raise RuntimeError("Set INTERNAL_UPDATE_TOKEN in .env or pass --internal-token")
    if not args.login or not args.password:
        raise RuntimeError("Set ADMINVPS_LOCAL_LOGIN/ADMINVPS_LOCAL_PASSWORD in .env or pass --login/--password")

    balance = run_browser(args.login, args.password, args.dashboard_url, args.wait_login_seconds)
    push_balance(args.base_url, args.internal_token, args.tg_id, args.label, balance, args.currency)
    print(f"Balance pushed: {balance} {args.currency} for {args.label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
