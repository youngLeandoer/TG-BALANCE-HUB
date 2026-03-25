import httpx

from src.core.logger import setup_logger
from src.core.security import security_service
from src.services.base import BaseAPIConnector, ServiceBalanceData

logger = setup_logger(__name__)


class WazzupConnector(BaseAPIConnector):
    """
    Wazzup API v3 connector.

    We don't have "money balance" here; instead we show:
    - balance: count of active channels
    - error_message: "not_enough_money=<count>" to show unpaid channels in /status
    """

    service_name = "wazzup"
    service_icon = "🟣"

    API_URL = "https://api.wazzup24.com/v3/channels"
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
            token = self._resolve_token().strip()
            if not token:
                return False
            headers = {"Authorization": f"Bearer {token}"}
            async with httpx.AsyncClient(timeout=self.REQUEST_TIMEOUT) as client:
                resp = await client.get(self.API_URL, headers=headers)
                return resp.status_code == 200
        except Exception as e:
            logger.error(f"Wazzup test_connection failed: {type(e).__name__}: {e}")
            return False

    async def get_balance_data(self) -> ServiceBalanceData:
        try:
            token = self._resolve_token().strip()
            if not token:
                return ServiceBalanceData(
                    balance=0.0,
                    currency="channels",
                    status="ERROR",
                    error_message="Пустой API-токен Wazzup",
                )

            headers = {"Authorization": f"Bearer {token}"}
            async with httpx.AsyncClient(timeout=self.REQUEST_TIMEOUT) as client:
                resp = await client.get(self.API_URL, headers=headers)
                resp.raise_for_status()
                data = resp.json()

            if not isinstance(data, list):
                return ServiceBalanceData(
                    balance=0.0,
                    currency="channels",
                    status="ERROR",
                    error_message="Невалидный ответ Wazzup (ожидался список каналов)",
                )

            active = 0
            unpaid = 0
            for item in data:
                if not isinstance(item, dict):
                    continue
                state = (item.get("state") or "").strip()
                if state == "active":
                    active += 1
                if state == "notEnoughMoney":
                    unpaid += 1

            return ServiceBalanceData(
                balance=float(active),
                currency="channels",
                status="OK",
                error_message=f"not_enough_money={unpaid}",
            )

        except httpx.HTTPStatusError as e:
            return ServiceBalanceData(
                balance=0.0,
                currency="channels",
                status="ERROR",
                error_message=f"HTTP {e.response.status_code}: {e.response.text[:160]}",
            )
        except Exception as e:
            logger.error(f"Wazzup unexpected error: {type(e).__name__}: {e}")
            return ServiceBalanceData(
                balance=0.0,
                currency="channels",
                status="ERROR",
                error_message=f"Ошибка ({type(e).__name__}): {str(e) or 'без деталей'}",
            )

