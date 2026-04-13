#!/usr/bin/env python3
"""
Local NIC.RU browser helper.

1) Opens browser (Playwright, headed)
2) Logs into NIC.RU manager (autofill or manual)
3) Parses balance from page HTML
4) POST balance to Balance Hub: /internal/nic/balance

Because NIC UI may change, on parse failure it dumps logs/nic_debug.html and logs/nic_debug.png.
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
    # Prefer the "Баланс договора -> Доступно -> <amount>" block in the manager UI.
    r"Баланс\s+договора[\s\S]{0,2000}?Доступно[\s\S]{0,300}?([-+]?\d[\d\s\u00a0\u202f]*[.,]?\d*)",
    r"Доступно[\s\S]{0,300}?([-+]?\d[\d\s\u00a0\u202f]*[.,]?\d*)\s*<span>₽</span>",
    r"Баланс[^0-9\-]*([-+]?\d[\d\s\u00a0\u202f]*[.,]?\d*)",
    r"balance[^0-9\-]*([-+]?\d[\d\s\u00a0\u202f]*[.,]?\d*)",
    r"RUR[^0-9\-]*([-+]?\d[\d\s\u00a0\u202f]*[.,]?\d*)",
    r"RUB[^0-9\-]*([-+]?\d[\d\s\u00a0\u202f]*[.,]?\d*)",
)


def parse_balance(text: str) -> float:
    for pattern in BALANCE_PATTERNS:
        m = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if m:
            raw = (
                m.group(1)
                .replace("\u202f", "")
                .replace("\xa0", "")
                .replace(" ", "")
                .replace(",", ".")
            )
            return float(raw)
    raise RuntimeError("Balance not found on page")


def safe_goto(page, url: str, timeout: int = 30000, attempts: int = 3) -> None:
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            # NIC pages can keep background connections alive; avoid strict networkidle.
            page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            return
        except Exception as exc:
            last_exc = exc
            if "ERR_NETWORK_CHANGED" not in str(exc) or attempt == attempts:
                raise
            time.sleep(1.5)
    if last_exc:
        raise last_exc


def _pick_logs_dir() -> str:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    preferred = os.path.join(repo_root, "logs")
    try:
        os.makedirs(preferred, exist_ok=True)
        test_path = os.path.join(preferred, ".write_test")
        with open(test_path, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(test_path)
        return preferred
    except Exception:
        fallback = os.path.join("/tmp", "tg-balance-hub-logs")
        os.makedirs(fallback, exist_ok=True)
        return fallback


def _dump_debug(page, *, prefix: str) -> tuple[str | None, str | None]:
    logs_dir = _pick_logs_dir()
    html_path = os.path.join(logs_dir, f"{prefix}.html")
    png_path = os.path.join(logs_dir, f"{prefix}.png")
    try:
        html = ""
        try:
            html = page.content() or ""
        except Exception:
            html = ""
        if not html:
            try:
                html = page.evaluate("() => document.documentElement.outerHTML") or ""
            except Exception:
                html = ""
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
    except Exception:
        html_path = None
    try:
        page.screenshot(path=png_path, full_page=True)
    except Exception:
        png_path = None
    return html_path, png_path


def run_browser(
    login: Optional[str],
    password: Optional[str],
    login_url: str,
    manager_url: str,
    wait_login_seconds: int,
) -> float:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. Run: pip install playwright && playwright install chromium"
        ) from exc

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=bool(os.getenv("PLAYWRIGHT_HEADLESS", "") == "1"))
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        safe_goto(page, login_url, timeout=30000, attempts=3)

        # Close cookie banner if it blocks clicks.
        try:
            page.locator("text=Руцентр использует").first.wait_for(timeout=1500)
            page.locator("button:has-text('×'), button[aria-label='Close'], [data-qa='CookieClose']").first.click(timeout=1500)
        except Exception:
            pass

        # Wait until login step is rendered.
        try:
            page.wait_for_selector(
                "input[placeholder*='NIC-D'], input[placeholder*='NIC-REG'], input[name='login'], input[type='text']",
                timeout=20000,
            )
        except Exception:
            html_path, png_path = _dump_debug(page, prefix="nic_debug_login_not_loaded")
            browser.close()
            hint = ", ".join([p for p in [png_path, html_path] if p]) or "debug files"
            raise RuntimeError(f"NIC login form did not load. Check {hint}.") from None

        if login and password:
            # NIC login is often a 2-step form: login/contract -> Next -> password -> Sign in.
            login_selectors = (
                "input#login",
                "input[name='login']",
                "input[placeholder*='NIC-D']",
                "input[placeholder*='NIC-REG']",
                "input[type='text']",
            )
            for selector in login_selectors:
                loc = page.locator(selector)
                if loc.count() == 0:
                    continue

                filled = False
                for i in range(min(int(loc.count()), 5)):
                    candidate = loc.nth(i)
                    try:
                        if not candidate.is_visible():
                            continue
                    except Exception:
                        continue
                    try:
                        candidate.scroll_into_view_if_needed(timeout=5000)
                    except Exception:
                        pass
                    try:
                        candidate.click(timeout=5000)
                    except Exception:
                        pass
                    try:
                        candidate.fill(login, timeout=30000)
                        filled = True
                        break
                    except Exception:
                        # if this input is overlapped/disabled, try next match/selector
                        continue

                if filled:
                    break
            else:
                html_path, png_path = _dump_debug(page, prefix="nic_debug_login_not_visible")
                browser.close()
                hint = ", ".join([p for p in [png_path, html_path] if p]) or "debug files"
                raise RuntimeError(f"NIC login input not visible/editable. Check {hint}.") from None

            # Click Next if present.
            next_btn = page.locator("button:has-text('Далее'), button:has-text('Next')").first
            if next_btn.count() > 0:
                try:
                    next_btn.click(timeout=5000)
                except Exception:
                    pass

            # Wait for password field to appear (up to 20s).
            try:
                page.wait_for_selector("input[type='password'], #password, input[name='password']", timeout=20000)
            except Exception:
                # Some flows may already show password or require captcha; continue best-effort.
                pass

            for selector in ("#password", "input[name='password']", "input[type='password']"):
                if page.locator(selector).count() > 0:
                    page.fill(selector, password)
                    break

            # Submit
            for selector in (
                "button[type='submit']",
                "button:has-text('Войти')",
                "button:has-text('Login')",
                "button:has-text('Далее')",
            ):
                if page.locator(selector).count() > 0:
                    try:
                        page.click(selector)
                    except Exception:
                        pass
                    break

        # Wait for potential redirects / manual steps (captcha/2FA).
        deadline = time.time() + max(int(wait_login_seconds), 10)
        while time.time() < deadline:
            page.wait_for_timeout(800)
            try:
                content = page.content()
            except Exception:
                content = ""
            if ("password" not in content.lower()) and ("войти" not in content.lower()):
                break

        # After login, go to NIC manager where balance is shown.
        safe_goto(page, manager_url, timeout=30000, attempts=3)
        page.wait_for_timeout(1500)
        content = page.content()
        try:
            bal = parse_balance(content)
            browser.close()
            return bal
        except Exception:
            html_path, png_path = _dump_debug(page, prefix="nic_debug")
            browser.close()
            hint = []
            if png_path:
                hint.append(png_path)
            if html_path:
                hint.append(html_path)
            where = ", ".join(hint) if hint else "debug files"
            raise RuntimeError(
                f"Balance not found. Check {where} and provide balance HTML snippet."
            ) from None


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
            "HTTP 404 «Workspace user not found»: в БД нет строки workspace. "
            "NIC_LOCAL_TG_ID должен совпадать с SHARED_WORKSPACE_TG_ID (или первым BOT_ADMINS)."
        )
    if status_code == 404 and "with this label not found" in detail:
        return (
            "HTTP 404: нет nic_scraper с таким label. "
            "NIC_LOCAL_LABEL = часть до первого «|» при /add."
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
    url = f"{base_url.rstrip('/')}/internal/nic/balance"
    with httpx.Client(timeout=20.0) as client:
        resp = client.post(url, json=payload, headers=headers)
        if resp.is_error:
            raise RuntimeError(_push_error_message(resp.status_code, url, resp.text)) from None


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="NIC.RU local browser balance fetcher")
    parser.add_argument("--tg-id", type=int, default=int(os.getenv("NIC_LOCAL_TG_ID", "0")))
    parser.add_argument("--label", default=os.getenv("NIC_LOCAL_LABEL", "Main"))
    parser.add_argument("--internal-token", default=os.getenv("INTERNAL_UPDATE_TOKEN", ""))
    parser.add_argument("--base-url", default=os.getenv("NIC_LOCAL_BASE_URL", "http://localhost:8000"))
    parser.add_argument(
        "--dashboard-url",
        default=os.getenv("NIC_DASHBOARD_URL", "https://www.nic.ru/auth/login/"),
        help="Login URL (defaults to NIC auth/login).",
    )
    parser.add_argument(
        "--manager-url",
        default=os.getenv("NIC_MANAGER_URL", "https://www.nic.ru/manager/"),
        help="Manager URL where balance should be parsed from.",
    )
    parser.add_argument("--currency", default=os.getenv("NIC_LOCAL_CURRENCY", "RUB"))
    parser.add_argument(
        "--wait-login-seconds",
        type=int,
        default=int(os.getenv("NIC_WAIT_LOGIN_SECONDS", "60")),
        help="Seconds to wait for login/redirect (manual steps/captcha).",
    )
    parser.add_argument("--headless", action="store_true", help="Run browser headless (no X server needed)")
    parser.add_argument("--headed", action="store_true", help="Run browser with UI (requires X server)")
    parser.add_argument("--login", default=os.getenv("NIC_LOCAL_LOGIN"))
    parser.add_argument("--password", default=os.getenv("NIC_LOCAL_PASSWORD"))
    args = parser.parse_args()

    if args.headless and args.headed:
        raise RuntimeError("Pass only one: --headless or --headed")
    if args.headless:
        os.environ["PLAYWRIGHT_HEADLESS"] = "1"
    if args.headed:
        os.environ["PLAYWRIGHT_HEADLESS"] = "0"

    if not args.tg_id:
        raise RuntimeError("Set NIC_LOCAL_TG_ID in .env or pass --tg-id")
    if not args.internal_token:
        raise RuntimeError("Set INTERNAL_UPDATE_TOKEN in .env or pass --internal-token")
    if not args.login or not args.password:
        raise RuntimeError("Set NIC_LOCAL_LOGIN/NIC_LOCAL_PASSWORD in .env or pass --login/--password")

    balance = run_browser(args.login, args.password, args.dashboard_url, args.manager_url, args.wait_login_seconds)
    push_balance(args.base_url, args.internal_token, args.tg_id, args.label, balance, args.currency)
    print(f"Balance pushed: {balance} {args.currency} for {args.label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

