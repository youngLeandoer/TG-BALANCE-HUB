import httpx
from src.services.base import BaseAPIConnector, ServiceBalanceData
from src.core.logger import setup_logger
from src.core.security import security_service

logger = setup_logger(__name__)


class AvitoConnector(BaseAPIConnector):
    """Коннектор для получения баланса кошелька Avito."""

    service_name = "avito"
    service_icon = "🟠"
    TOKEN_URL = "https://api.avito.ru/token"
    API_BASE_URL = "https://api.avito.ru"
    REQUEST_TIMEOUT = httpx.Timeout(connect=8.0, read=12.0, write=8.0, pool=5.0)

    BALANCE_PATH_CANDIDATES = (
        "/core/v1/accounts/{user_id}/balance",
        "/core/v1/users/{user_id}/balance",
        "/core/v1/user/{user_id}/balance",
    )

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
    def _parse_credentials(raw_key: str) -> tuple[str, str, str]:
        value = (raw_key or "").strip()
        parts = value.split(":")
        if len(parts) != 3:
            raise ValueError("Для Avito используйте формат: user_id:client_id:client_secret")
        user_id, client_id, client_secret = (p.strip() for p in parts)
        if not user_id.isdigit():
            raise ValueError("Для Avito user_id должен быть числом")
        if not client_id or not client_secret:
            raise ValueError("Для Avito client_id и client_secret обязательны")
        return user_id, client_id, client_secret

    async def _get_access_token(self, client_id: str, client_secret: str) -> str:
        async with httpx.AsyncClient(timeout=self.REQUEST_TIMEOUT) as client:
            response = await client.post(
                self.TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "scope": "user_balance:read",
                },
            )
            response.raise_for_status()
            data = response.json()
            token = data.get("access_token")
            if not token:
                raise RuntimeError("Avito token response does not contain access_token")
            return token

    async def _request_balance(self, user_id: str, access_token: str) -> dict:
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        errors: list[str] = []

        async with httpx.AsyncClient(timeout=self.REQUEST_TIMEOUT) as client:
            for path in self.BALANCE_PATH_CANDIDATES:
                url = f"{self.API_BASE_URL}{path.format(user_id=user_id)}"
                logger.info(f"Avito balance try url={url}")
                response = await client.get(url, headers=headers)
                logger.info(f"Avito response status={response.status_code} url={url}")
                if response.status_code == 200:
                    return response.json()
                if response.status_code in (401, 403):
                    response.raise_for_status()
                if response.status_code == 404:
                    errors.append(url)
                    continue
                response.raise_for_status()

        raise RuntimeError(f"Avito balance endpoint not found. Tried: {', '.join(errors)}")

    async def test_connection(self) -> bool:
        try:
            raw_key = self._resolve_raw_key()
            user_id, client_id, client_secret = self._parse_credentials(raw_key)
            token = await self._get_access_token(client_id, client_secret)
            data = await self._request_balance(user_id, token)
            return isinstance(data, dict)
        except Exception as e:
            logger.error(f"Avito test_connection failed: {type(e).__name__}: {e}")
            return False

    async def get_balance_data(self) -> ServiceBalanceData:
        try:
            raw_key = self._resolve_raw_key()
            user_id, client_id, client_secret = self._parse_credentials(raw_key)
            token = await self._get_access_token(client_id, client_secret)
            data = await self._request_balance(user_id, token)

            # Defensive parsing: support common swagger shapes.
            amount = 0.0
            currency = "RUB"
            payload = data.get("result", data) if isinstance(data, dict) else {}
            if isinstance(payload, dict):
                if isinstance(payload.get("balance"), (int, float, str)):
                    amount = float(payload.get("balance", 0) or 0)
                elif isinstance(payload.get("money"), (int, float, str)):
                    amount = float(payload.get("money", 0) or 0)
                elif isinstance(payload.get("real"), (int, float, str)):
                    amount = float(payload.get("real", 0) or 0)
                currency = str(payload.get("currency", "RUB")).upper()

            return ServiceBalanceData(
                balance=amount,
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
            logger.error(f"Avito unexpected error: {type(e).__name__}: {e}")
            return ServiceBalanceData(
                balance=0.0,
                currency="RUB",
                status="ERROR",
                error_message=f"Ошибка ({type(e).__name__}): {str(e) or 'без деталей'}",
            )
