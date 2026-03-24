import httpx
import base64
from src.services.base import BaseAPIConnector, ServiceBalanceData
from src.core.logger import setup_logger
from src.core.security import security_service

logger = setup_logger(__name__)


class HosterByConnector(BaseAPIConnector):
    """Коннектор для проверки баланса hoster.by"""

    service_name = "hosterby"
    service_icon = "🇧🇾"
    BASE_URLS = ("https://api.hoster.by",)
    API_PATH_PREFIXES = ("",)
    API_VERSIONS = ("1",)
    REQUEST_TIMEOUT = httpx.Timeout(connect=5.0, read=8.0, write=8.0, pool=5.0)

    def _auth_headers_candidates(self, api_key: str) -> tuple[dict, ...]:
        return (
            {"Access-Token": api_key, "Accept": "application/json"},
            {"x-api-token": api_key, "Accept": "application/json"},
        )

    async def _request_json(
        self,
        client: httpx.AsyncClient,
        method: str,
        path: str,
        api_key: str,
    ) -> dict:
        last_response = None
        last_transport_error: Exception | None = None
        logger.info(f"HosterBy request start: {method} {path}")
        for base_url in self.BASE_URLS:
            for prefix in self.API_PATH_PREFIXES:
                url = f"{base_url}{prefix}{path}"
                for headers in self._auth_headers_candidates(api_key):
                    # Some docs require `version` query param in each request.
                    param_sets = ({},)
                    if "x-api-token" in headers or "X-API-Token" in headers:
                        param_sets = tuple(({"version": v} for v in self.API_VERSIONS)) + ({},)

                    for params in param_sets:
                        try:
                            logger.info(
                                f"HosterBy try url={url} header={list(headers.keys())[0]} params={params or {}}"
                            )
                            response = await client.request(method, url, headers=headers, params=params)
                        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as exc:
                            last_transport_error = exc
                            logger.warning(f"HosterBy transport issue url={url}: {type(exc).__name__}")
                            continue
                        last_response = response
                        logger.info(f"HosterBy response status={response.status_code} url={url}")
                        if response.status_code == 200:
                            return response.json()
                        if 400 <= response.status_code < 600:
                            response.raise_for_status()
        if last_response is not None:
            last_response.raise_for_status()
        if last_transport_error is not None:
            raise last_transport_error
        raise RuntimeError("No response from hoster API")

    async def _fetch_cloud_order_balance(self, api_key: str) -> dict:
        last_error: Exception | None = None
        for _ in range(1):
            try:
                async with httpx.AsyncClient(timeout=self.REQUEST_TIMEOUT, follow_redirects=True) as client:
                    orders_data = await self._request_json(client, "GET", "/cloud/orders", api_key)
                    payload = orders_data.get("payload", {}) if isinstance(orders_data, dict) else {}
                    orders = payload.get("orders", []) if isinstance(payload, dict) else []
                    if not orders:
                        raise RuntimeError("Не найдено ни одного cloud order в аккаунте")

                    order_id = str(orders[0].get("id", "")).strip()
                    if not order_id:
                        raise RuntimeError("Не удалось определить orderId из /cloud/orders")

                    return await self._request_json(
                        client,
                        "GET",
                        f"/cloud/orders/{order_id}/balance",
                        api_key,
                    )
            except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as exc:
                last_error = exc
                continue
        if last_error:
            raise last_error
        raise RuntimeError("Unknown transport error")


    @staticmethod
    def _normalize_hoster_key(value: str) -> str:
        """Hoster key may come as raw hex or base64/base64url-encoded hex."""
        if not isinstance(value, str):
            return value

        raw = value.strip()
        if not raw:
            return raw

        # Already looks like regular hex token.
        if all(ch in "0123456789abcdefABCDEF" for ch in raw):
            return raw.lower()

        # Try base64/base64url decode, then use decoded hex if it matches.
        try:
            padded = raw + "=" * (-len(raw) % 4)
            decoded = base64.urlsafe_b64decode(padded.encode()).decode().strip()
            if decoded and all(ch in "0123456789abcdefABCDEF" for ch in decoded):
                return decoded.lower()
        except Exception:
            pass

        return raw

    def _resolve_api_key(self) -> str:
        encrypted_key = self.credentials.get("api_key", "")
        try:
            decrypted = security_service.decrypt(encrypted_key)
            return self._normalize_hoster_key(decrypted)
        except Exception:
            if isinstance(encrypted_key, str) and encrypted_key:
                logger.warning("Using plaintext api_key from credentials")
                return self._normalize_hoster_key(encrypted_key)
            raise

    async def test_connection(self) -> bool:
        try:
            api_key = self._resolve_api_key()
            async with httpx.AsyncClient(timeout=self.REQUEST_TIMEOUT, follow_redirects=True) as client:
                data = await self._request_json(client, "GET", "/cloud/orders", api_key)
                return isinstance(data, dict)
        except Exception as e:
            logger.error(f"HosterBy test_connection failed: {type(e).__name__}: {e}")
            return False

    async def get_balance_data(self) -> ServiceBalanceData:
        try:
            api_key = self._resolve_api_key()
            logger.info("HosterBy balance check started")
            data = await self._fetch_cloud_order_balance(api_key)

            payload = data.get("payload", {}) if isinstance(data, dict) else {}
            orders = payload.get("orders", []) if isinstance(payload, dict) else []
            first_order = orders[0] if orders else {}
            balance_raw = first_order.get("balance", "0")
            currency = payload.get("currency", "BYN")

            try:
                balance = float(balance_raw)
            except (TypeError, ValueError):
                balance = 0.0

            return ServiceBalanceData(
                balance=balance,
                currency=currency,
                status="OK",
            )

        except httpx.HTTPStatusError as e:
            logger.error(f"HosterBy HTTP error: {e.response.status_code} - {e.response.text[:200]}")
            request_url = str(e.request.url) if e.request else ""
            if e.response.status_code == 404:
                return ServiceBalanceData(
                    balance=0.0,
                    currency="BYN",
                    status="ERROR",
                    error_message="Cloud API endpoint не найден на api.hoster.by (ожидался /cloud/orders)",
                )
            return ServiceBalanceData(
                balance=0.0,
                currency="BYN",
                status="ERROR",
                error_message=f"HTTP {e.response.status_code}{f' ({request_url})' if request_url else ''}: {e.response.text[:120]}",
            )
        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as e:
            logger.error(f"HosterBy transport error: {type(e).__name__}: {e}")
            return ServiceBalanceData(
                balance=0.0,
                currency="BYN",
                status="ERROR",
                error_message="Сеть: не удалось подключиться к Cloud API api.hoster.by",
            )
        except Exception as e:
            logger.error(f"HosterBy unexpected error: {type(e).__name__}: {e}")
            return ServiceBalanceData(
                balance=0.0,
                currency="BYN",
                status="ERROR",
                error_message=f"Ошибка ({type(e).__name__}): {str(e) or 'без деталей'}",
            )
