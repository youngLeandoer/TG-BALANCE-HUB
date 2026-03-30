import httpx

from src.core.logger import setup_logger
from src.core.security import security_service
from src.services.base import BaseAPIConnector, ServiceBalanceData

logger = setup_logger(__name__)


class TimewebCloudConnector(BaseAPIConnector):
    """
    Timeweb Cloud API connector.

    Uses: GET https://api.timeweb.cloud/api/v1/account/finances
    Auth: Authorization: Bearer <token>
    Docs: https://timeweb.cloud/api-docs (also reflected in sdk-php PaymentsApi->getFinances)
    """

    service_name = "timewebcloud"
    service_icon = "☁️"

    API_URL = "https://api.timeweb.cloud/api/v1/account/finances"
    REQUEST_TIMEOUT = httpx.Timeout(connect=8.0, read=12.0, write=8.0, pool=5.0)

    def _resolve_token(self) -> str:
        encrypted_key = self.credentials.get("api_key", "")
        try:
            return security_service.decrypt(encrypted_key)
        except Exception:
            if isinstance(encrypted_key, str) and encrypted_key:
                logger.warning("Using plaintext api_key from credentials")
                return encrypted_key
            raise

    async def test_connection(self) -> bool:
        try:
            token = (self._resolve_token() or "").strip()
            if not token:
                return False
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            async with httpx.AsyncClient(timeout=self.REQUEST_TIMEOUT) as client:
                resp = await client.get(self.API_URL, headers=headers)
                return resp.status_code == 200
        except Exception as e:
            logger.error(f"Timeweb Cloud test_connection failed: {type(e).__name__}: {e}")
            return False

    async def get_balance_data(self) -> ServiceBalanceData:
        try:
            token = (self._resolve_token() or "").strip()
            if not token:
                return ServiceBalanceData(
                    balance=0.0,
                    currency="RUB",
                    status="ERROR",
                    error_message="Пустой API-токен Timeweb Cloud",
                )

            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            async with httpx.AsyncClient(timeout=self.REQUEST_TIMEOUT) as client:
                resp = await client.get(self.API_URL, headers=headers)
                resp.raise_for_status()
                data = resp.json()

            finances = data.get("finances", data) if isinstance(data, dict) else {}
            if not isinstance(finances, dict):
                return ServiceBalanceData(
                    balance=0.0,
                    currency="RUB",
                    status="ERROR",
                    error_message="Невалидный ответ Timeweb Cloud (ожидался объект finances)",
                )

            balance = float(finances.get("balance", 0.0) or 0.0)
            currency = str(finances.get("currency", "RUB") or "RUB").upper()

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
            logger.error(f"Timeweb Cloud unexpected error: {type(e).__name__}: {e}")
            return ServiceBalanceData(
                balance=0.0,
                currency="RUB",
                status="ERROR",
                error_message=f"Ошибка ({type(e).__name__}): {str(e) or 'без деталей'}",
            )

