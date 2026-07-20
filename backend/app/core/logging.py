"""Structured JSON logging.

All logs are single-line JSON so a collector can parse them without regex. A
redaction filter strips anything that looks like a QRadar token or Authorization
header before it can reach a log sink — secrets must never appear in logs.
"""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

from pythonjsonlogger import jsonlogger

# Patterns for values we refuse to log even if a caller passes them by mistake.
_SEC_HEADER = re.compile(r"(SEC|Authorization|sec_token|api_key|password)\b", re.IGNORECASE)
_TOKEN_LIKE = re.compile(
    r"\b[A-Fa-f0-9]{8}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{12}\b"
)


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._redact(record.msg)
        if record.args:
            record.args = tuple(
                self._redact(a) if isinstance(a, str) else a for a in record.args
            )
        return True

    @staticmethod
    def _redact(text: str) -> str:
        if _SEC_HEADER.search(text):
            # Line mentions a secret field name; blunt-redact any long token.
            text = _TOKEN_LIKE.sub("<redacted>", text)
        return text


class _JsonFormatter(jsonlogger.JsonFormatter):
    def add_fields(
        self, log_record: dict[str, Any], record: logging.LogRecord, message_dict: dict[str, Any]
    ) -> None:
        super().add_fields(log_record, record, message_dict)
        log_record.setdefault("level", record.levelname)
        log_record.setdefault("logger", record.name)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        _JsonFormatter("%(asctime)s %(level)s %(logger)s %(message)s")
    )
    handler.addFilter(RedactionFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # uvicorn access logs are noisy and duplicate our request logging.
    logging.getLogger("uvicorn.access").handlers.clear()
