import httpx
from src.services.base import BaseAPIConnector, ServiceBalanceData
from src.core.logger import setup_logger
from src.core.security import security_service

logger = setup_logger(__name__)


class RegRuConnector(BaseAPIConnector):
    """Connector for REG.RU account balance via REG.API 2."""

    service_name = "regru"
    service_icon = "🌐"
    API_BASE = "https://api.reg.ru/api/regru2"
    REQUEST_TIMEOUT = httpx.Timeout(connect=8.0, read=12.0, write=8.0, pool=5.0)
    BALANCE_ENDPOINTS = (
        "/user/get_balance",
        "/bill/get_balance",
        "/billing/get_balance",
        "/user/balance",
    )

    def _resolve_raw_key(self) -> str:
        encrypted_key = self.credentials.get("api_key", "")
        try:
            return security_service.decrypt(encrypted_key)
        except Exception:
            if isinstance(encrypted_key, str) and encrypted_key:
                logger.warning("Using plaintext REG.RU credentials from storage")
                return encrypted_key
            raise

    @staticmethod
    def _parse_auth(raw_key: str) -> tuple[str, str]:
        value = (raw_key or "").strip()
        if ":" not in value:
            raise ValueError("Для REG.RU используйте формат: login:password")
        login, password = value.split(":", 1)
        login = login.strip()
        password = password.strip()
        if not login or not password:
            raise ValueError("Для REG.RU используйте формат: login:password")
        return login, password

    async def _request_balance_payload(self, login: str, password: str) -> dict:
        last_error: Exception | None = None
        errors: list[str] = []
        form_data = {
            "username": login,
            "password": password,
            "output_content_type": "application/json",
            "output_format": "json",
        }

        async with httpx.AsyncClient(timeout=self.REQUEST_TIMEOUT, follow_redirects=True) as client:
            for endpoint in self.BALANCE_ENDPOINTS:
                url = f"{self.API_BASE}{endpoint}"
                try:
                    logger.info(f"REG.RU try endpoint={endpoint}")
                    response = await client.post(url, data=form_data)
                    if response.status_code == 404:
                        errors.append(endpoint)
                        continue
                    response.raise_for_status()
                    data = response.json()
                    if isinstance(data, dict):
                        return data
                    raise RuntimeError("REG.RU API returned non-object response")
                except httpx.HTTPStatusError as exc:
                    last_error = exc
                    if exc.response.status_code == 404:
                        errors.append(endpoint)
                        continue
                    raise
                except Exception as exc:
                    last_error = exc

        if errors and len(errors) == len(self.BALANCE_ENDPOINTS):
            raise RuntimeError(
                "Не найден endpoint баланса REG.RU API. "
                "Проверьте доступность метода в вашем аккаунте API."
            )
        if last_error:
            raise last_error
        raise RuntimeError("REG.RU API did not return response")

    @staticmethod
    def _extract_balance(data: dict) -> tuple[float, str]:
        payload = data.get("answer", data) if isinstance(data, dict) else {}
        candidates = ("balance", "prepay", "real_balance", "amount", "money")
        amount = None

        if isinstance(payload, dict):
            for key in candidates:
                value = payload.get(key)
                if isinstance(value, (int, float, str)):
                    try:
                        amount = float(str(value).replace(",", "."))
                        break
                    except ValueError:
                        continue

        if amount is None:
            amount = 0.0
        currency = "RUB"
        if isinstance(payload, dict):
            cur = payload.get("currency")
            if isinstance(cur, str) and cur.strip():
                currency = cur.strip().upper()
        return amount, currency

    async def test_connection(self) -> bool:
        try:
            raw_key = self._resolve_raw_key()
            login, password = self._parse_auth(raw_key)
            data = await self._request_balance_payload(login, password)
            result = str(data.get("result", "")).lower() if isinstance(data, dict) else ""
            return result == "success" or isinstance(data, dict)
        except Exception as e:
            logger.error(f"REG.RU test_connection failed: {type(e).__name__}: {e}")
            return False

    async def get_balance_data(self) -> ServiceBalanceData:
        try:
            raw_key = self._resolve_raw_key()
            login, password = self._parse_auth(raw_key)
            data = await self._request_balance_payload(login, password)
            result = str(data.get("result", "")).lower() if isinstance(data, dict) else ""
            if result and result != "success":
                return ServiceBalanceData(
                    balance=0.0,
                    currency="RUB",
                    status="ERROR",
                    error_message=f"REG.RU API error: {data.get('error_text') or data.get('error_code') or 'unknown'}",
                )

            balance, currency = self._extract_balance(data)
            return ServiceBalanceData(
                balance=balance,
                currency=currency,
                status="OK",
            )
        except httpx.HTTPStatusError as e:
            return ServiceBalanceData(
                balance=0.0,
                currency="RUB",
                status="ERROR",
                error_message=f"HTTP {e.response.status_code}: {e.response.text[:120]}",
            )
        except (ValueError, TypeError) as e:
            return ServiceBalanceData(
                balance=0.0,
                currency="RUB",
                status="ERROR",
                error_message=str(e),
            )
        except Exception as e:
            logger.error(f"REG.RU unexpected error: {type(e).__name__}: {e}")
            return ServiceBalanceData(
                balance=0.0,
                currency="RUB",
                status="ERROR",
                error_message=f"Ошибка ({type(e).__name__}): {str(e) or 'без деталей'}",
            )
