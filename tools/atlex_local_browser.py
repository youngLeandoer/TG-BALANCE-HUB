#!/usr/bin/env python3
"""
Local ATLEX browser helper.

1) Opens browser (Playwright, headed)
2) Logs into ATLEX personal account (autofill or manual)
3) Parses balance from page HTML
4) POST balance to Balance Hub: /internal/atlex/balance

Example:
  python tools/atlex_local_browser.py
(env: ATLEX_LOCAL_TG_ID, ATLEX_LOCAL_LABEL, INTERNAL_UPDATE_TOKEN,
 ATLEX_LOCAL_BASE_URL, ATLEX_LOCAL_LOGIN, ATLEX_LOCAL_PASSWORD, ATLEX_DASHBOARD_URL)
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
    # ATLEX cabinet often renders as a small table: "RUR:" then "<b>693.00 руб</b>"
    r"RUR:\s*</td>\s*<td[^>]*>\s*<b>\s*([-+]?\d[\d\s]*[.,]?\d*)\s*(?:руб|р\.?)",
    r"Баланс[^0-9\-]*([-+]?\d[\d\s]*[.,]\d{1,2})",
    r"Balance[^0-9\-]*([-+]?\d[\d\s]*[.,]\d{1,2})",
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


def _dump_debug_artifacts(page, *, prefix: str) -> None:
    try:
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        logs_dir = os.path.join(repo_root, "logs")
        os.makedirs(logs_dir, exist_ok=True)
        html_path = os.path.join(logs_dir, f"{prefix}.html")
        png_path = os.path.join(logs_dir, f"{prefix}.png")
        try:
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(page.content())
        except Exception:
            pass
        try:
            page.screenshot(path=png_path, full_page=True)
        except Exception:
            pass
    except Exception:
        pass


def _maybe_select_client(page, client_contains: Optional[str]) -> None:
    """
    ATLEX login may require selecting a "Клиент" from a dropdown.
    We try to pick the first visible <select> that has an option containing client_contains.
    """
    if not client_contains or not client_contains.strip():
        return
    hint = client_contains.strip()

    def try_select_from(sel) -> bool:
        # Some portals populate options lazily; click select to trigger.
        try:
            sel.click(timeout=800)
        except Exception:
            pass

        deadline_ms = 6000
        step_ms = 200
        waited = 0
        while waited <= deadline_ms:
            options = sel.locator("option")
            for j in range(options.count()):
                opt = options.nth(j)
                try:
                    text = (opt.text_content() or "").strip()
                except Exception:
                    continue
                if hint.lower() in text.lower():
                    try:
                        value = opt.get_attribute("value")
                    except Exception:
                        value = None
                    if value is not None:
                        sel.select_option(value=value)
                    else:
                        sel.select_option(label=text)
                    page.wait_for_timeout(300)
                    return True
            page.wait_for_timeout(step_ms)
            waited += step_ms
        return False

    # 1) Best effort: find select near "Клиент" label.
    try:
        direct = page.locator("#clientid").first
        if direct.count() > 0 and try_select_from(direct):
            return

        client_label = page.locator("text=Клиент").first
        if client_label.count() > 0:
            labeled_select = client_label.locator("xpath=following::select[1]").first
            if labeled_select.count() > 0 and try_select_from(labeled_select):
                return
    except Exception:
        pass

    # 2) Fallback: scan all selects on the page.
    selects = page.locator("select")
    for i in range(selects.count()):
        sel = selects.nth(i)
        try:
            if try_select_from(sel):
                return
        except Exception:
            continue

    # 3) Last resort: custom dropdowns (combobox/listbox).
    try:
        combo = page.locator("[role='combobox'], [aria-label*='Клиент'], [name*='client']").first
        if combo.count() > 0:
            combo.click(timeout=1500)
            page.wait_for_timeout(200)
            option = page.locator(f"text={hint}").first
            if option.count() > 0:
                option.click(timeout=1500)
                page.wait_for_timeout(300)
                return
    except Exception:
        pass


def run_browser(
    login: Optional[str],
    password: Optional[str],
    dashboard_url: str,
    client_contains: Optional[str],
    client_id: Optional[str],
    wait_login_seconds: int,
) -> float:
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

        if login and password:
            for selector in ("input[name='login']", "input[type='email']", "#login", "input[name='username']"):
                if page.locator(selector).count() > 0:
                    page.fill(selector, login)
                    # Some forms reveal dependent fields only after blur/change.
                    try:
                        page.locator(selector).press("Tab")
                    except Exception:
                        pass
                    break
            for selector in ("input[name='password']", "input[type='password']", "#password"):
                if page.locator(selector).count() > 0:
                    page.fill(selector, password)
                    try:
                        page.locator(selector).press("Tab")
                    except Exception:
                        pass
                    break

            submit_btn = page.locator("#submit").first
            if submit_btn.count() == 0:
                submit_btn = page.locator("button[type='submit']").first

            def _click_submit() -> None:
                submit_btn.click(timeout=5000)

            # ATLEX login is an AJAX flow:
            # - first submit may return authStatus=2 and reveal client selector
            # - after selecting client, you must submit again
            _click_submit()

            # Wait briefly for either client selector, captcha, MFA, or redirect.
            # Prefer fast waits over long sleeps.
            try:
                page.wait_for_timeout(300)
            except Exception:
                pass

            # If client selector appears, select and submit again.
            try:
                page.wait_for_selector("#showClients", state="visible", timeout=5000)
                # ensure options are populated
                try:
                    page.wait_for_selector("#clientid option[value]:not([value=''])", timeout=5000)
                except Exception:
                    pass

                if client_id and str(client_id).strip():
                    page.locator("#clientid").select_option(value=str(client_id).strip())
                else:
                    _maybe_select_client(page, client_contains)

                _click_submit()
            except Exception:
                # no client selector - continue (could be single client, captcha, MFA, etc.)
                pass

        # Give time for login flow (captcha/OTP/manual steps may exist).
        deadline = time.time() + max(int(wait_login_seconds), 10)
        while time.time() < deadline:
            page.wait_for_timeout(800)
            content = ""
            try:
                content = page.content()
            except Exception:
                content = ""

            # Heuristic: if login form is gone, proceed.
            if ("Пароль" not in content) and ("Клиент" not in content) and ("Войти" not in content):
                break
            # Sometimes login succeeds but page still contains the word "Войти" elsewhere;
            # check for logout hints.
            if ("Выход" in content) or ("Logout" in content):
                break

        # Always try to re-open dashboard after login attempts.
        safe_goto(page, dashboard_url, timeout=30000, attempts=3)
        page.wait_for_timeout(2000)
        content = page.content()

        try:
            bal = parse_balance(content)
            browser.close()
            return bal
        except RuntimeError:
            _dump_debug_artifacts(page, prefix="atlex_debug")
            # If we are still on login screen, make error actionable.
            if "Клиент" in content and ("Пароль" in content or "Login" in content or "Войти" in content):
                browser.close()
                raise RuntimeError(
                    "Balance not found (still on login/selection screen). "
                    "Check logs/atlex_debug.png and logs/atlex_debug.html. "
                    "If there is captcha/confirmation code, complete it manually or adjust selectors. "
                    "Also ensure ATLEX_LOCAL_CLIENT_ID=6947 or ATLEX_LOCAL_CLIENT matches option text."
                ) from None
            browser.close()
            raise


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
            "ATLEX_LOCAL_TG_ID должен совпадать с вашим Telegram ID."
        )
    if status_code == 404 and "with this label not found" in detail:
        return (
            "HTTP 404: нет atlex_scraper с таким label. "
            "ATLEX_LOCAL_LABEL = часть до первого «|» при /add."
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
    url = f"{base_url.rstrip('/')}/internal/atlex/balance"
    with httpx.Client(timeout=20.0) as client:
        resp = client.post(url, json=payload, headers=headers)
        if resp.is_error:
            raise RuntimeError(_push_error_message(resp.status_code, url, resp.text)) from None


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="ATLEX local browser balance fetcher")
    parser.add_argument("--tg-id", type=int, default=int(os.getenv("ATLEX_LOCAL_TG_ID", "0")))
    parser.add_argument("--label", default=os.getenv("ATLEX_LOCAL_LABEL", "Main"))
    parser.add_argument("--internal-token", default=os.getenv("INTERNAL_UPDATE_TOKEN", ""))
    parser.add_argument("--base-url", default=os.getenv("ATLEX_LOCAL_BASE_URL", "http://localhost:8000"))
    parser.add_argument("--dashboard-url", default=os.getenv("ATLEX_DASHBOARD_URL", "https://client.atlex.ru/"))
    parser.add_argument("--currency", default=os.getenv("ATLEX_LOCAL_CURRENCY", "RUB"))
    parser.add_argument(
        "--client",
        default=os.getenv("ATLEX_LOCAL_CLIENT", ""),
        help="Client dropdown option substring (e.g. ООО \"АТРИ\"). Optional.",
    )
    parser.add_argument(
        "--client-id",
        default=os.getenv("ATLEX_LOCAL_CLIENT_ID", ""),
        help="Client dropdown option value (e.g. 6947). Preferred if known.",
    )
    parser.add_argument(
        "--wait-login-seconds",
        type=int,
        default=int(os.getenv("ATLEX_WAIT_LOGIN_SECONDS", "90")),
        help="Seconds to wait for manual/captcha/OTP during login flow.",
    )
    parser.add_argument("--login", default=os.getenv("ATLEX_LOCAL_LOGIN"))
    parser.add_argument("--password", default=os.getenv("ATLEX_LOCAL_PASSWORD"))
    args = parser.parse_args()

    if not args.tg_id:
        raise RuntimeError("Set ATLEX_LOCAL_TG_ID in .env or pass --tg-id")
    if not args.internal_token:
        raise RuntimeError("Set INTERNAL_UPDATE_TOKEN in .env or pass --internal-token")
    if not args.login or not args.password:
        raise RuntimeError("Set ATLEX_LOCAL_LOGIN/ATLEX_LOCAL_PASSWORD in .env or pass --login/--password")

    balance = run_browser(
        args.login,
        args.password,
        args.dashboard_url,
        args.client,
        args.client_id,
        args.wait_login_seconds,
    )
    push_balance(args.base_url, args.internal_token, args.tg_id, args.label, balance, args.currency)
    print(f"Balance pushed: {balance} {args.currency} for {args.label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

