"""Structured logging configuration for LoopCoder.

Single entry point: ``configure_logging(level, log_dir)``. Call once at the
top of the CLI; every module gets a logger via ``logging.getLogger(__name__)``.

Decisions:
- Plain format on stderr for humans (timestamp + level + module + msg).
- JSON-lines mirror to ``<log_dir>/loopcoder-<date>.log`` for tooling.
- Third-party noise (httpx, openai retries, GitPython) muted to WARNING.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from datetime import datetime
from pathlib import Path


_HUMAN_FMT = "%(asctime)s %(levelname)-7s %(name)s :: %(message)s"
_DATE_FMT = "%H:%M:%S"


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:  # type: ignore[override]
        payload = {
            "ts": datetime.utcfromtimestamp(record.created).isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for k in ("session_id", "goal_id", "iter", "tool"):
            v = getattr(record, k, None)
            if v is not None:
                payload[k] = v
        return json.dumps(payload, ensure_ascii=False, default=str)


_NOISY_LOGGERS = ["httpx", "httpcore", "openai", "openai._base_client", "git", "urllib3"]


def configure_logging(level: str | int = "INFO", log_dir: str | Path | None = None) -> None:
    """Attach a stderr handler and (optionally) a JSON file handler."""
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    # Reset any existing handlers (idempotent)
    for h in list(root.handlers):
        root.removeHandler(h)

    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(logging.Formatter(_HUMAN_FMT, datefmt=_DATE_FMT))
    stream.setLevel(level)
    root.addHandler(stream)

    if log_dir:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        fname = Path(log_dir) / f"loopcoder-{datetime.utcnow():%Y%m%d}.log"
        fh = logging.handlers.RotatingFileHandler(
            fname, maxBytes=20 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        fh.setFormatter(_JsonFormatter())
        fh.setLevel(level)
        root.addHandler(fh)

    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(max(level, logging.WARNING))


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
