"""JARVIS logging configuration.

Features added:
  - Structured JSON formatter for file handler (machine-readable)
  - Rolling file handler with 500 MB cap and 5 backup files
  - LogSanitizeFilter wired to all file handlers (CWE-117 defence)
  - Subsystem-tagged format: [subsystem] LEVEL message
"""

import json
import logging
import logging.handlers
import sys
import time
from pathlib import Path


# ── JSON formatter ────────────────────────────────────────────────────────────

class _JsonFormatter(logging.Formatter):
    """Emit one JSON object per log line (machine-parseable)."""

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        msg = record.getMessage()
        doc = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created)),
            "level": record.levelname,
            "name": record.name,
            "msg": msg,
        }
        if record.exc_info:
            doc["exc"] = self.formatException(record.exc_info)
        return json.dumps(doc, ensure_ascii=False)


# ── Setup ─────────────────────────────────────────────────────────────────────

def setup_logging(
    level: str = "INFO",
    log_file: str | None = None,
    quiet: bool = False,
    json_file: bool = True,
    max_bytes: int = 500 * 1024 * 1024,  # 500 MB
    backup_count: int = 5,
) -> None:
    """Configure logging for JARVIS.

    Args:
        level:        Root log level (default "INFO").
        log_file:     Path for the plain-text log file.  When omitted the
                      path from src.config.LOG_FILE is used.
        quiet:        If True, suppress stderr output.  Used by CLI to keep
                      the terminal clean.
        json_file:    If True (default), also write a structured .jsonl log
                      alongside the plain-text log.
        max_bytes:    Rolling file size cap (default 500 MB).
        backup_count: Number of rotated log files to keep (default 5).
    """
    from src.utils.log_sanitize import LogSanitizeFilter

    plain_fmt = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    datefmt = "%H:%M:%S"

    handlers: list[logging.Handler] = []

    # ── stderr ──
    if not quiet:
        stream_h = logging.StreamHandler(sys.stderr)
        stream_h.setFormatter(logging.Formatter(plain_fmt, datefmt=datefmt))
        handlers.append(stream_h)

    # ── rotating plain-text file ──
    if log_file is None:
        try:
            from src.config import LOG_FILE
            log_file = str(LOG_FILE)
        except ImportError:
            log_file = str(Path.home() / ".jarvis" / "jarvis.log")

    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        file_h = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_h.setFormatter(logging.Formatter(plain_fmt, datefmt=datefmt))
        file_h.addFilter(LogSanitizeFilter())
        handlers.append(file_h)

        # ── JSON structured log (same dir, .jsonl extension) ──
        if json_file:
            json_path = str(Path(log_file).with_suffix(".jsonl"))
            json_h = logging.handlers.RotatingFileHandler(
                json_path,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
            json_h.setFormatter(_JsonFormatter())
            json_h.addFilter(LogSanitizeFilter())
            handlers.append(json_h)

    if not handlers:
        handlers.append(logging.NullHandler())

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        handlers=handlers,
        force=True,
    )

    # Silence noisy third-party loggers
    for noisy in [
        "httpx", "httpcore", "numexpr", "numexpr.utils",
        "anthropic", "anthropic._base_client",
        "urllib3", "urllib3.connectionpool",
    ]:
        logging.getLogger(noisy).setLevel(logging.ERROR)
