import logging
import os
import sys
from logging.handlers import RotatingFileHandler

import structlog

_LOG_DIR = os.environ.get("LOG_DIR", "/app/logs")
_LOG_MAX_BYTES = 50 * 1024 * 1024   # 50 MB per file
_LOG_BACKUP_COUNT = 5                # keep last 5 files = max 250 MB


def setup_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # Always log to stdout (for docker logs)
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(stdout_handler)

    # Rotate log files if LOG_DIR is writable
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
        file_handler = RotatingFileHandler(
            os.path.join(_LOG_DIR, "app.log"),
            maxBytes=_LOG_MAX_BYTES,
            backupCount=_LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(logging.Formatter("%(message)s"))
        root.addHandler(file_handler)
    except OSError:
        pass  # read-only filesystem or missing dir — stdout only is fine

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
