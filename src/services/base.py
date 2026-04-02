from abc import ABC, abstractmethod
from typing import Optional
from datetime import datetime
from pydantic import BaseModel


class ServiceBalanceData(BaseModel):
    balance: float
    currency: str
    expiration: Optional[datetime] = None
    status: str = "OK"
    error_message: Optional[str] = None


class BaseAPIConnector(ABC):
    service_name: str = "Generic"
    service_icon: str = "🔌"
    
    def __init__(self, credentials: dict):
        self.credentials = credentials
    
    @abstractmethod
    async def test_connection(self) -> bool:
        pass
    
    @abstractmethod
    async def get_balance_data(self) -> ServiceBalanceData:
        pass


def connector_wait_timeout_seconds(service_name: str) -> float:
    """
    Upper bound for asyncio.wait_for around get_balance_data().
    Some providers need two sequential HTTP calls or retry several auth variants.
    """
    if service_name == "hosterby":
        return 40.0
    return 12.0
