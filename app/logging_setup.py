"""File-based logging with defensive redaction of personal data and secrets.

Redaction is a safety net only. Code must still avoid passing personal data to loggers.
"""

import logging
import os
import re
from io import TextIOWrapper
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FILE_NAME = "puln_assistant.log"
_HANDLER_NAME = "puln-assistant-file"

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Indonesian mobile numbers: 08..., 628..., +628..., with optional space, dot or dash separators.
_PHONE = re.compile(r"(?<![\w+])(?:\+62|62|0)[\s.-]?8\d{1,3}(?:[\s.-]?\d{3,4}){2}(?!\d)")
_SECRET_HEADER = re.compile(r"(?im)^((?:set-)?cookie|authorization)(\s*:\s*).+$")
_SECRET_PARAM = re.compile(r"(?i)\b(cf-turnstile-response|submission_key)(\s*[=:]\s*)[^&\s]+")


def redact(text: str) -> str:
    """Mask e-mail addresses, phone numbers, cookies, tokens and submission keys."""
    text = _SECRET_HEADER.sub(r"\1\2[redacted-secret]", text)
    text = _SECRET_PARAM.sub(r"\1\2[redacted-secret]", text)
    text = _EMAIL.sub("[redacted-email]", text)
    return _PHONE.sub("[redacted-phone]", text)


class _PrivateRotatingFileHandler(RotatingFileHandler):
    """Rotating file handler whose current log file is readable by the owner only."""

    def _open(self) -> TextIOWrapper:
        stream = super()._open()
        os.chmod(self.baseFilename, 0o600)
        return stream


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        return True


def configure_logging(log_dir: Path, level: str = "INFO") -> None:
    """Send logs to a rotating file only, so console output stays free for the dashboard."""
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    for existing in [h for h in root.handlers if h.get_name() == _HANDLER_NAME]:
        root.removeHandler(existing)
        existing.close()

    handler = _PrivateRotatingFileHandler(
        log_dir / LOG_FILE_NAME, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.set_name(_HANDLER_NAME)
    handler.addFilter(RedactingFilter())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(level)
    # Our client logs each request with its outcome; skip the library's duplicate lines.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
