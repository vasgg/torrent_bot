import logging.config
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    TOKEN: SecretStr
    ADMINS: list[int]
    FOLDER: str

    model_config = SettingsConfigDict(
        env_prefix='BOT_',
        env_file='.env',
        env_file_encoding='utf-8',
        case_sensitive=False,
        extra='allow'
    )


template = {
    "format": "%(asctime)s.%(msecs)03d [%(levelname)8s] [%(module)s:%(funcName)s:%(lineno)d] %(message)s",
    "datefmt": "%d.%m.%Y %H:%M:%S%z",
}


def setup_logs(app_name: str):
    Path("logs").mkdir(parents=True, exist_ok=True)
    logging_config = get_logging_config(app_name)
    logging.config.dictConfig(logging_config)


def get_logging_config(app_name: str):
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "main": template,
            "errors": template,
        },
        "handlers": {
            "stdout": {
                "class": "logging.StreamHandler",
                "level": "INFO",
                "formatter": "main",
                "stream": sys.stdout,
            },
            "stderr": {
                "class": "logging.StreamHandler",
                "level": "WARNING",
                "formatter": "errors",
                "stream": sys.stderr,
            },
            "file": {
                "()": RotatingFileHandler,
                "level": "INFO",
                "formatter": "main",
                "filename": f"logs/{app_name}.log",
                "maxBytes": 50000000,
                "backupCount": 3,
                "encoding": "utf-8",
            },
        },
        "loggers": {
            "root": {
                "level": "DEBUG",
                "handlers": ["stdout", "stderr", "file"],
            },
        },
    }
