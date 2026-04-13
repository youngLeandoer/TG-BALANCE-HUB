import httpx

from src.core.logger import setup_logger
from src.core.security import security_service
from src.services.base import BaseAPIConnector, ServiceBalanceData

logger = setup_logger(__name__)


class YandexCloudConnector(BaseAPIConnector):
    """
    Yandex Cloud Billing connector.

    Reads current balance from BillingAccount resource.
    Base URL: https://billing.api.cloud.yandex.net
    Model: BillingAccount has fields: balance (string), currency (RUB/USD/KZT).
    Ref: https://sdk-docs.nikolaymatrosov.ru/docs/billing/v1/types/

    Credentials format in our storage: billing_account_id:token
    Token can be IAM token or OAuth token (sent as Authorization: Bearer <token>).
    """

    service_name = "yandex_cloud"
    service_icon = "🟨"

    API_BASE = "https://billing.api.cloud.yandex.net/billing/v1"
    REQUEST_TIMEOUT = httpx.Timeout(connect=8.0, read=12.0, write=8.0, pool=5.0)

    def _resolve_raw_key(self) -> str:
        encrypted_key = self.credentials.get("api_key", "")
        try:
            return security_service.decrypt(encrypted_key)
        except Exception:
            if isinstance(encrypted_key, str) and encrypted_key:
                logger.warning("Using plaintext Yandex Cloud credentials from storage")
                return encrypted_key
            raise

    @staticmethod
    def _parse_auth(raw_key: str) -> tuple[str, str]:
        value = (raw_key or "").strip()
        if ":" not in value:
            raise ValueError("Для Yandex Cloud используйте формат: billing_account_id:token")
        billing_account_id, token = value.split(":", 1)
        billing_account_id = billing_account_id.strip()
        token = token.strip()
        if not billing_account_id or not token:
            raise ValueError("Для Yandex Cloud используйте формат: billing_account_id:token")
        return billing_account_id, token

    async def _request_billing_account(self, billing_account_id: str, token: str) -> dict:
        url = f"{self.API_BASE}/billingAccounts/{billing_account_id}"
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        async with httpx.AsyncClient(timeout=self.REQUEST_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, dict):
                raise RuntimeError("Yandex Cloud Billing API returned non-object response")
            return data

    async def test_connection(self) -> bool:
        try:
            raw_key = self._resolve_raw_key()
            billing_account_id, token = self._parse_auth(raw_key)
            data = await self._request_billing_account(billing_account_id, token)
            return "balance" in data or isinstance(data, dict)
        except Exception as e:
            logger.error(f"Yandex Cloud test_connection failed: {type(e).__name__}: {e}")
            return False

    @staticmethod
    def _extract_balance_and_currency(data: dict) -> tuple[float, str]:
        currency = "RUB"
        cur = data.get("currency")
        if isinstance(cur, str) and cur.strip():
            currency = cur.strip().upper()

        raw_balance = data.get("balance")
        if raw_balance is None:
            return 0.0, currency
        try:
            # balance is a string in API; keep it robust (commas etc.)
            amount = float(str(raw_balance).replace(",", "."))
        except Exception:
            amount = 0.0
        return amount, currency

    async def get_balance_data(self) -> ServiceBalanceData:
        try:
            raw_key = self._resolve_raw_key()
            billing_account_id, token = self._parse_auth(raw_key)
            data = await self._request_billing_account(billing_account_id, token)
            balance, currency = self._extract_balance_and_currency(data)
            return ServiceBalanceData(balance=balance, currency=currency, status="OK")
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
            logger.error(f"Yandex Cloud unexpected error: {type(e).__name__}: {e}")
            return ServiceBalanceData(
                balance=0.0,
                currency="RUB",
                status="ERROR",
                error_message=f"Ошибка ({type(e).__name__}): {str(e) or 'без деталей'}",
            )

