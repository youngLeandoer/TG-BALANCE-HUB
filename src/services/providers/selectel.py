import httpx

from src.core.logger import setup_logger
from src.core.security import security_service
from src.services.base import BaseAPIConnector, ServiceBalanceData

logger = setup_logger(__name__)


class SelectelConnector(BaseAPIConnector):
    """
    Selectel Billing API connector.

    Endpoint: GET https://api.selectel.ru/v3/balances
    Auth:
      - Static token: X-Token: <token>
      - IAM account token: X-Auth-Token: <token>
    Docs:
      - https://docs.selectel.ru/en/api/balance/
      - https://docs.selectel.ru/en/api/authorization/
    """

    service_name = "selectel"
    service_icon = "🟦"

    API_URL = "https://api.selectel.ru/v3/balances"
    REQUEST_TIMEOUT = httpx.Timeout(connect=8.0, read=12.0, write=8.0, pool=5.0)

    def _resolve_token(self) -> str:
        encrypted_key = self.credentials.get("api_key", "")
        try:
            return security_service.decrypt(encrypted_key)
        except Exception:
            if isinstance(encrypted_key, str) and encrypted_key:
                logger.warning("Using plaintext Selectel token from credentials")
                return encrypted_key
            raise

    async def _request_balances(self, token: str) -> httpx.Response:
        # Prefer static token (X-Token), fallback to IAM token (X-Auth-Token) if unauthorized.
        headers_primary = {"X-Token": token, "Accept": "application/json"}
        headers_fallback = {"X-Auth-Token": token, "Accept": "application/json"}
        async with httpx.AsyncClient(timeout=self.REQUEST_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(self.API_URL, headers=headers_primary)
            if resp.status_code == 401:
                resp = await client.get(self.API_URL, headers=headers_fallback)
            return resp

    async def test_connection(self) -> bool:
        try:
            token = (self._resolve_token() or "").strip()
            if not token:
                return False
            resp = await self._request_balances(token)
            return resp.status_code == 200
        except Exception as e:
            logger.error(f"Selectel test_connection failed: {type(e).__name__}: {e}")
            return False

    @staticmethod
    def _extract_balance_and_currency(payload: dict) -> tuple[float, str]:
        # Expected: {"status": "...", "data": {"settings": {"currency": "RUB"}, "billings":[...]}}
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            return 0.0, "RUB"

        currency = "RUB"
        settings = data.get("settings")
        if isinstance(settings, dict):
            cur = settings.get("currency")
            if isinstance(cur, str) and cur.strip():
                currency = cur.strip().upper()

        billings = data.get("billings")
        if isinstance(billings, list) and billings:
            total = 0.0
            for b in billings:
                if not isinstance(b, dict):
                    continue
                value = b.get("balances_values_sum")
                if value is None:
                    value = b.get("final_sum")
                if value is None and isinstance(b.get("balances"), list):
                    value = sum(
                        float(x.get("value", 0) or 0)
                        for x in b.get("balances", [])
                        if isinstance(x, dict)
                    )
                try:
                    total += float(value or 0.0)
                except Exception:
                    continue
            return total, currency

        return 0.0, currency

    async def get_balance_data(self) -> ServiceBalanceData:
        try:
            token = (self._resolve_token() or "").strip()
            if not token:
                return ServiceBalanceData(
                    balance=0.0,
                    currency="RUB",
                    status="ERROR",
                    error_message="Пустой токен Selectel",
                )

            resp = await self._request_balances(token)
            resp.raise_for_status()
            payload = resp.json() if resp.content else {}
            balance, currency = self._extract_balance_and_currency(payload if isinstance(payload, dict) else {})

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
                error_message=f"HTTP {e.response.status_code}: {e.response.text[:160]}",
            )
        except (ValueError, TypeError) as e:
            return ServiceBalanceData(
                balance=0.0,
                currency="RUB",
                status="ERROR",
                error_message=str(e),
            )
        except Exception as e:
            logger.error(f"Selectel unexpected error: {type(e).__name__}: {e}")
            return ServiceBalanceData(
                balance=0.0,
                currency="RUB",
                status="ERROR",
                error_message=f"Ошибка ({type(e).__name__}): {str(e) or 'без деталей'}",
            )

