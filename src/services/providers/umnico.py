import httpx
import json
from typing import Optional
from datetime import datetime
from src.services.base import BaseAPIConnector, ServiceBalanceData
from src.core.logger import setup_logger
from src.core.security import security_service

logger = setup_logger(__name__)


class UmnicoConnector(BaseAPIConnector):
    """Коннектор для проверки баланса Umnico"""
    
    service_name = "umnico"
    service_icon = "💬"
    
    API_URL = "https://api.umnico.com/v1.3/account/me/tariff"
    INTEGRATIONS_URL = "https://api.umnico.com/v1.3/integrations"

    def _resolve_api_key(self) -> str:
        encrypted_key = self.credentials.get('api_key', '')
        try:
            return security_service.decrypt(encrypted_key)
        except Exception:
            if isinstance(encrypted_key, str) and encrypted_key.startswith("eyJ"):
                logger.warning("Using plaintext JWT api_key from credentials")
                return encrypted_key
            raise

    async def _request_tariff(self, client: httpx.AsyncClient, api_key: str) -> httpx.Response:
        # Some environments accept raw JWT, some require Bearer prefix.
        for token in (api_key, f"Bearer {api_key}"):
            headers = self._get_headers(token)
            response = await client.get(self.API_URL, headers=headers)
            if response.status_code != 401:
                return response
        return response

    async def _request_integrations(self, client: httpx.AsyncClient, api_key: str) -> httpx.Response:
        for token in (api_key, f"Bearer {api_key}"):
            headers = self._get_headers(token)
            response = await client.get(self.INTEGRATIONS_URL, headers=headers)
            if response.status_code != 401:
                return response
        return response
    
    async def test_connection(self) -> bool:
        """Проверка валидности API-ключа"""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                api_key = self._resolve_api_key()
                token_preview = self.credentials.get('api_key', '')[:20] + '...' if self.credentials.get('api_key') else 'EMPTY'
                logger.info(f"🔌 TEST REQUEST to: {self.API_URL}")
                logger.info(f"🔑 Token preview: {token_preview}")
                response = await self._request_tariff(client, api_key)
                
                logger.info(f"📡 RESPONSE status: {response.status_code}")
                logger.info(f"📄 RESPONSE body: {response.text[:300]}")
                
                return response.status_code in [200, 201]
                
        except Exception as e:
            logger.error(f"❌ Connection test failed: {type(e).__name__}: {e}")
            return False
    
    async def get_balance_data(self) -> ServiceBalanceData:
        """Получение данных о балансе"""
        try:
            encrypted_key = self.credentials.get('api_key', '')
            logger.info(f"🔐 Encrypted key preview: {encrypted_key[:30] if encrypted_key else 'EMPTY'}...")
            try:
                api_key = self._resolve_api_key()
                logger.info(f"✅ Decrypted key preview: {api_key[:20]}...")
            except Exception as decrypt_error:
                logger.error(f"❌ Decryption failed: {type(decrypt_error).__name__}: {decrypt_error}")
                return ServiceBalanceData(
                    balance=0.0,
                    currency="RUB",
                    status="ERROR",
                    error_message=f"Ошибка расшифровки: {str(decrypt_error)}"
                )
            
            async with httpx.AsyncClient(timeout=10.0) as client:
                logger.info(f"🔌 REQUEST to: {self.API_URL}")
                logger.info("📦 Trying Authorization formats: raw JWT, then Bearer JWT")
                response = await self._request_tariff(client, api_key)
                
                logger.info(f"📡 RESPONSE status: {response.status_code}")
                logger.info(f"📄 RESPONSE body: {response.text[:500]}")
                
                response.raise_for_status()
                data = response.json()
                
                balance_str = data.get('value', '0')
                balance = float(balance_str) if balance_str else 0.0
                currency = data.get('currency', 'RUB')
                
                # Дата окончания тарифа
                expiration_raw = (
                    data.get("valid_to")
                    or data.get("validTo")
                    or data.get("expiration")
                    or data.get("expires_at")
                    or data.get("expired_at")
                    or data.get("expire_at")
                    or data.get("expireAt")
                    or data.get("expiration_at")
                    or data.get("expirationAt")
                )
                expiration = None
                if expiration_raw:
                    try:
                        if isinstance(expiration_raw, (int, float)):
                            expiration = datetime.fromtimestamp(float(expiration_raw))
                        else:
                            expiration_str = str(expiration_raw).strip()
                            # Common formats: ISO8601 with Z / with offset.
                            expiration = datetime.fromisoformat(expiration_str.replace("Z", "+00:00"))
                    except Exception:
                        expiration = None
                
                logger.info(f"✅ Balance parsed: {balance} {currency}")

                # Also fetch integration channel statuses (active/inactive channels).
                channels_active = None
                channels_total = None
                active_channels: list[str] = []
                inactive_channels: list[str] = []
                try:
                    integrations_resp = await self._request_integrations(client, api_key)
                    integrations_resp.raise_for_status()
                    integrations = integrations_resp.json()
                    if isinstance(integrations, list):
                        channels_total = len(integrations)
                        channels_active = 0
                        for item in integrations:
                            if not isinstance(item, dict):
                                continue
                            status = (item.get("status") or "").strip()
                            itype = (item.get("type") or "").strip()
                            login = (item.get("login") or "").strip()
                            label = ""
                            if itype and login:
                                label = f"{itype}:{login}"
                            elif login:
                                label = login
                            elif itype:
                                label = itype

                            if status == "active":
                                channels_active += 1
                                if label:
                                    active_channels.append(label)
                            else:
                                if label:
                                    inactive_channels.append(f"{label} ({status or 'unknown'})")
                except Exception as exc:
                    logger.warning(f"Umnico integrations fetch failed: {type(exc).__name__}: {exc}")
                
                return ServiceBalanceData(
                    balance=balance,
                    currency=currency,
                    expiration=expiration,
                    status="OK",
                    error_message=(
                        json.dumps(
                            {
                                "channels_active": channels_active,
                                "channels_total": channels_total,
                                "active_channels": active_channels[:10],
                                "active_channels_more": max(len(active_channels) - 10, 0),
                                "inactive_channels": inactive_channels[:10],
                                "inactive_channels_more": max(len(inactive_channels) - 10, 0),
                            },
                            ensure_ascii=False,
                        )
                        if channels_active is not None and channels_total is not None
                        else None
                    ),
                )
                
        except httpx.HTTPStatusError as e:
            logger.error(f"❌ HTTP error: {e.response.status_code} - {e.response.text}")
            return ServiceBalanceData(
                balance=0.0,
                currency="RUB",
                status="ERROR",
                error_message=f"HTTP {e.response.status_code}: {e.response.text[:100]}"
            )
        except httpx.RequestError as e:
            logger.error(f"❌ Request error: {type(e).__name__}: {e}")
            return ServiceBalanceData(
                balance=0.0,
                currency="RUB",
                status="ERROR",
                error_message=f"Ошибка сети: {str(e)}"
            )
        except Exception as e:
            logger.error(f"❌ Unexpected error: {type(e).__name__}: {e}")
            return ServiceBalanceData(
                balance=0.0,
                currency="RUB",
                status="ERROR",
                error_message=f"Ошибка: {str(e)}"
            )
    
    def _get_headers(self, api_key: str) -> dict:
        """Получение заголовков для API запроса"""
        return {
            'Authorization': api_key,  # JWT без префикса
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }