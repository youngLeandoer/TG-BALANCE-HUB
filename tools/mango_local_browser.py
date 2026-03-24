#!/usr/bin/env python3
"""
Local Mango Office browser helper.

What it does:
1) Opens real browser (Playwright, headed mode)
2) Logs into Mango Office manually/automatically
3) Extracts balance from page content
4) Pushes balance into Balance Hub internal endpoint

Usage example:
python tools/mango_local_browser.py \
  --tg-id 123456789 \
  --label "Mango Main" \
  --internal-token "YOUR_TOKEN" \
  --login "user@example.com" \
  --password "secret"
"""

import argparse
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


def run_browser(login: Optional[str], password: Optional[str], dashboard_url: str) -> float:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. Run: pip install playwright && playwright install chromium"
        ) from exc

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        safe_goto(page, dashboard_url, timeout=30000, attempts=3)

        # Optional basic autofill. If selectors don't match, user can finish manually.
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

        # Give user time for OTP/2FA if needed.
        page.wait_for_timeout(15000)
        safe_goto(page, dashboard_url, timeout=30000, attempts=3)
        content = page.content()
        browser.close()
        return parse_balance(content)


def push_balance(base_url: str, token: str, tg_id: int, label: str, balance: float, currency: str) -> None:
    payload = {
        "tg_id": tg_id,
        "label": label,
        "balance": balance,
        "currency": currency,
    }
    headers = {"x-internal-token": token}
    with httpx.Client(timeout=20.0) as client:
        resp = client.post(f"{base_url.rstrip('/')}/internal/mango/balance", json=payload, headers=headers)
        resp.raise_for_status()


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Mango local browser balance fetcher")
    parser.add_argument("--tg-id", type=int, default=int(os.getenv("MANGO_LOCAL_TG_ID", "0")))
    parser.add_argument("--label", default=os.getenv("MANGO_LOCAL_LABEL", "Mango Main"))
    parser.add_argument("--internal-token", default=os.getenv("INTERNAL_UPDATE_TOKEN", ""))
    parser.add_argument("--base-url", default=os.getenv("MANGO_LOCAL_BASE_URL", "http://localhost:8000"))
    parser.add_argument("--dashboard-url", default=os.getenv("MANGO_DASHBOARD_URL", "https://lk.mango-office.ru/"))
    parser.add_argument("--currency", default=os.getenv("MANGO_LOCAL_CURRENCY", "RUB"))
    parser.add_argument("--login", default=os.getenv("MANGO_LOCAL_LOGIN"))
    parser.add_argument("--password", default=os.getenv("MANGO_LOCAL_PASSWORD"))
    args = parser.parse_args()

    if not args.tg_id:
        raise RuntimeError("Set MANGO_LOCAL_TG_ID in .env or pass --tg-id")
    if not args.internal_token:
        raise RuntimeError("Set INTERNAL_UPDATE_TOKEN in .env or pass --internal-token")
    if not args.login or not args.password:
        raise RuntimeError("Set MANGO_LOCAL_LOGIN/MANGO_LOCAL_PASSWORD in .env or pass --login/--password")

    balance = run_browser(args.login, args.password, args.dashboard_url)
    push_balance(args.base_url, args.internal_token, args.tg_id, args.label, balance, args.currency)
    print(f"Balance pushed: {balance} {args.currency} for {args.label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
