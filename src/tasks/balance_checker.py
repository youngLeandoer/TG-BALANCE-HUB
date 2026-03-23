from src.tasks import app
from src.core.logger import setup_logger

logger = setup_logger(__name__)


@app.task
def check_balance(service_id: int):
    """Задача для проверки баланса сервиса"""
    logger.info(f"Checking balance for service {service_id}")
    # TODO: Реализовать проверку баланса
    return {"status": "OK", "service_id": service_id}
    