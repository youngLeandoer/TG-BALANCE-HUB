import httpx
from src.services.base import BaseAPIConnector, ServiceBalanceData
from src.core.logger import setup_logger
from src.core.security import security_service

logger = setup_logger(__name__)


class SMSAeroConnector(BaseAPIConnector):
    """Коннектор для проверки баланса SMS Aero."""

    service_name = "smsaero"
    service_icon = "✉️"
    API_URL = "https://gate.smsaero.ru/v2/balance"
    REQUEST_TIMEOUT = httpx.Timeout(connect=8.0, read=10.0, write=8.0, pool=5.0)

    def _resolve_raw_key(self) -> str:
        encrypted_key = self.credentials.get("api_key", "")
        try:
            return security_service.decrypt(encrypted_key)
        except Exception:
            if isinstance(encrypted_key, str) and encrypted_key:
                logger.warning("Using plaintext api_key from credentials")
                return encrypted_key
            raise

    @staticmethod
    def _parse_auth(raw_key: str) -> tuple[str, str]:
        value = (raw_key or "").strip()
        if ":" not in value:
            raise ValueError("Для SMS Aero используйте формат ключа: email:api_key")
        email, api_key = value.split(":", 1)
        email = email.strip()
        api_key = api_key.strip()
        if not email or not api_key:
            raise ValueError("Для SMS Aero используйте формат ключа: email:api_key")
        return email, api_key

    async def test_connection(self) -> bool:
        try:
            raw_key = self._resolve_raw_key()
            email, api_key = self._parse_auth(raw_key)
            async with httpx.AsyncClient(timeout=self.REQUEST_TIMEOUT) as client:
                response = await client.get(self.API_URL, auth=(email, api_key))
                return response.status_code == 200
        except Exception as e:
            logger.error(f"SMS Aero test_connection failed: {type(e).__name__}: {e}")
            return False

    async def get_balance_data(self) -> ServiceBalanceData:
        try:
            raw_key = self._resolve_raw_key()
            email, api_key = self._parse_auth(raw_key)
            logger.info(f"SMS Aero balance check started for email={email}")
            async with httpx.AsyncClient(timeout=self.REQUEST_TIMEOUT) as client:
                response = await client.get(self.API_URL, auth=(email, api_key))
                logger.info(f"SMS Aero response status={response.status_code}")
                response.raise_for_status()
                data = response.json()

            if not isinstance(data, dict) or not data.get("success"):
                return ServiceBalanceData(
                    balance=0.0,
                    currency="RUB",
                    status="ERROR",
                    error_message=f"Ошибка SMS Aero: {data.get('message') if isinstance(data, dict) else 'невалидный ответ'}",
                )

            payload = data.get("data", {}) if isinstance(data, dict) else {}
            balance_raw = payload.get("balance", 0)
            balance = float(balance_raw)

            return ServiceBalanceData(
                balance=balance,
                currency="RUB",
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
            logger.error(f"SMS Aero unexpected error: {type(e).__name__}: {e}")
            return ServiceBalanceData(
                balance=0.0,
                currency="RUB",
                status="ERROR",
                error_message=f"Ошибка ({type(e).__name__}): {str(e) or 'без деталей'}",
            )
