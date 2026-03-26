import logging
import sys
import os
from datetime import datetime
from logging.handlers import RotatingFileHandler
from src.core.config import settings
from src.core.timezone import app_tz


class TZFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):  # noqa: N802
        dt = datetime.fromtimestamp(record.created, tz=app_tz())
        if datefmt:
            return dt.strftime(datefmt)
        return dt.isoformat()


def setup_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, settings.LOG_LEVEL.upper()))
    
    if not logger.handlers:
        formatter = TZFormatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

        log_dir = os.path.dirname(settings.LOG_FILE_PATH)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)

        file_handler = RotatingFileHandler(
            settings.LOG_FILE_PATH,
            maxBytes=5 * 1024 * 1024,  # 5MB
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    
    return logger
