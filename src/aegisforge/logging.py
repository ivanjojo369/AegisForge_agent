from __future__ import annotations

import logging as py_logging
from typing import Final


DEFAULT_LOG_FORMAT: Final[str] = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
DEFAULT_DATE_FORMAT: Final[str] = "%Y-%m-%d %H:%M:%S"


def normalize_log_level(level: str | None) -> int:
    if not level:
        return py_logging.INFO

    normalized = level.strip().upper()
    mapping = {
        "CRITICAL": py_logging.CRITICAL,
        "ERROR": py_logging.ERROR,
        "WARNING": py_logging.WARNING,
        "INFO": py_logging.INFO,
        "DEBUG": py_logging.DEBUG,
        "NOTSET": py_logging.NOTSET,
    }
    return mapping.get(normalized, py_logging.INFO)


def setup_logging(level: str | None = "INFO") -> None:
    root_logger = py_logging.getLogger()
    desired_level = normalize_log_level(level)

    if not root_logger.handlers:
        py_logging.basicConfig(
            level=desired_level,
            format=DEFAULT_LOG_FORMAT,
            datefmt=DEFAULT_DATE_FORMAT,
        )
    else:
        root_logger.setLevel(desired_level)
        for handler in root_logger.handlers:
            handler.setLevel(desired_level)


def get_logger(name: str) -> py_logging.Logger:
    return py_logging.getLogger(name)
