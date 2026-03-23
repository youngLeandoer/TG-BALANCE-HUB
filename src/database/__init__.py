import asyncio
import logging
from sqlalchemy.ext.asyncio import create_async_engine
from src.core.config import settings
from src.database.models import Base

logger = logging.getLogger(__name__)


async def init_db():
    """Создание всех таблиц при старте"""
    engine = create_async_engine(
        settings.database_url,
        echo=False,
        pool_pre_ping=True,
    )
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    logger.info("✅ Database tables created successfully!")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(init_db())