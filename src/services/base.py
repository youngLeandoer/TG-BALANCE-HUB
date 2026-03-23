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
