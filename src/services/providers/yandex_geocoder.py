import httpx

from src.core.logger import setup_logger
from src.core.security import security_service
from src.services.base import BaseAPIConnector, ServiceBalanceData

logger = setup_logger(__name__)


class YandexGeocoderConnector(BaseAPIConnector):
    """
    Yandex Geocoder API connector.

    Endpoint:
      - https://geocode-maps.yandex.ru/v1/?apikey=...&geocode=...&format=json
        Docs: https://yandex.com/dev/geocode/doc/en/request

    Important:
      - Geocoder generally does NOT provide a "balance" API.
      - If rate-limit headers are present, we surface them as a metric:
        X-RateLimit-Limit / X-RateLimit-Remaining (best-effort).
    """

    service_name = "yandex_geocoder"
    service_icon = "🧭"

    API_URL = "https://geocode-maps.yandex.ru/v1/"
    REQUEST_TIMEOUT = httpx.Timeout(connect=8.0, read=12.0, write=8.0, pool=5.0)

    def _resolve_token(self) -> str:
        encrypted_key = self.credentials.get("api_key", "")
        try:
            return security_service.decrypt(encrypted_key)
        except Exception:
            if isinstance(encrypted_key, str) and encrypted_key:
                logger.warning("Using plaintext Yandex Geocoder api_key from storage")
                return encrypted_key
            raise

    async def _probe(self, api_key: str) -> tuple[dict, dict]:
        params = {
            "apikey": api_key,
            "geocode": "Москва, Кремль",
            "format": "json",
            "lang": "ru_RU",
            "results": 1,
        }
        async with httpx.AsyncClient(timeout=self.REQUEST_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(self.API_URL, params=params, headers={"Accept": "application/json"})
            headers = dict(resp.headers)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, dict):
                raise RuntimeError("Geocoder API returned non-object response")
            return data, headers

    async def test_connection(self) -> bool:
        try:
            api_key = (self._resolve_token() or "").strip()
            if not api_key:
                return False
            await self._probe(api_key)
            return True
        except Exception as e:
            logger.error(f"Yandex Geocoder test_connection failed: {type(e).__name__}: {e}")
            return False

    @staticmethod
    def _extract_rl(headers: dict) -> tuple[float | None, float | None]:
        def _to_float(v: str | None) -> float | None:
            if not v:
                return None
            try:
                return float(str(v).strip())
            except Exception:
                return None

        limit = _to_float(headers.get("X-RateLimit-Limit") or headers.get("x-ratelimit-limit"))
        remaining = _to_float(headers.get("X-RateLimit-Remaining") or headers.get("x-ratelimit-remaining"))
        return limit, remaining

    async def get_balance_data(self) -> ServiceBalanceData:
        try:
            api_key = (self._resolve_token() or "").strip()
            if not api_key:
                return ServiceBalanceData(
                    balance=0.0,
                    currency="REQ",
                    status="ERROR",
                    error_message="Пустой API-ключ Yandex Geocoder",
                )

            _data, headers = await self._probe(api_key)
            limit, remaining = self._extract_rl(headers)

            if remaining is not None:
                # Use balance as "remaining requests" (metric), not money.
                msg = f"rate_limit={limit}" if limit is not None else None
                return ServiceBalanceData(balance=float(remaining), currency="REQ", status="OK", error_message=msg)

            return ServiceBalanceData(
                balance=1.0,
                currency="OK",
                status="OK",
                error_message="rate_limit_headers_missing",
            )
        except httpx.HTTPStatusError as e:
            return ServiceBalanceData(
                balance=0.0,
                currency="REQ",
                status="ERROR",
                error_message=f"HTTP {e.response.status_code}: {e.response.text[:160]}",
            )
        except (ValueError, TypeError) as e:
            return ServiceBalanceData(
                balance=0.0,
                currency="REQ",
                status="ERROR",
                error_message=str(e),
            )
        except Exception as e:
            logger.error(f"Yandex Geocoder unexpected error: {type(e).__name__}: {e}")
            return ServiceBalanceData(
                balance=0.0,
                currency="REQ",
                status="ERROR",
                error_message=f"Ошибка ({type(e).__name__}): {str(e) or 'без деталей'}",
            )

