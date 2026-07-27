import json
import logging
import re
import threading
import time
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import override

FATAL = 60
logging.addLevelName(FATAL, "FATAL")


class QBLogger(logging.Logger):
    def fatal(self, msg, *args, **kwargs):
        if self.isEnabledFor(FATAL):
            self._log(FATAL, msg, args, **kwargs)


logging.setLoggerClass(QBLogger)


class StructuredFormatter(logging.Formatter):
    def __init__(self, environment="production", json_format=True):
        super().__init__()
        self.environment = environment
        self.json_format = json_format
        self._text_fmt = "%(asctime)s.%(msecs)03d | %(levelname)-8s | %(name)15s | T:%(threadName)-10s | %(message)s"

    def format(self, record):
        if not self.json_format:
            return logging.Formatter(self._text_fmt).format(record)

        entry = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "module": record.name,
            "message": record.getMessage(),
            "thread": record.threadName,
            "environment": self.environment,
        }

        for key in (
            "request_id",
            "user_id",
            "operation",
            "torrent_hash",
            "torrent_name",
        ):
            val = getattr(record, key, None)
            if val is not None:
                entry[key] = val

        if record.exc_info and record.exc_info[0] is not None:
            entry["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            entry["stack_trace"] = self.formatStack(record.stack_info)

        return json.dumps(entry, ensure_ascii=False)


class SensitiveDataFilter(logging.Filter):
    _PATTERNS = [
        (re.compile(r"(password\s*[:=]\s*)\S+", re.IGNORECASE), r"\1***"),
        (re.compile(r"(token\s*[:=]\s*)\S+", re.IGNORECASE), r"\1***"),
        (re.compile(r"(secret\s*[:=]\s*)\S+", re.IGNORECASE), r"\1***"),
        (re.compile(r'(Authorization\s*[:=]\s*"\s*)[^"]+', re.IGNORECASE), r"\1***"),
    ]

    def __init__(self, enabled=True):
        super().__init__()
        self.enabled = enabled

    def filter(self, record):
        if self.enabled and isinstance(record.msg, str):
            for pat, repl in self._PATTERNS:
                record.msg = pat.sub(repl, record.msg)
        return True


class ContextFilter(logging.Filter):
    _local = threading.local()

    @classmethod
    def set(cls, **kwargs):
        if not hasattr(cls._local, "data"):
            cls._local.data = {}
        cls._local.data.update(kwargs)

    @classmethod
    def clear(cls):
        if hasattr(cls._local, "data"):
            cls._local.data.clear()

    def filter(self, record):
        data = getattr(self._local, "data", {})
        for key, val in data.items():
            setattr(record, key, val)
        return True


class SizeTimeRotatingFileHandler(TimedRotatingFileHandler):
    def __init__(self, filename, maxBytes=0, when="midnight", backupCount=0, **kwargs):  # noqa: N803
        super().__init__(filename, when=when, backupCount=backupCount, **kwargs)
        self.maxBytes = maxBytes

    @override
    def shouldRollover(self, record):  # noqa: N802
        if super().shouldRollover(record):
            return 1
        if self.maxBytes > 0:
            msg = f"{self.format(record)}\n"
            try:
                if self.stream is None:
                    self.stream = self._open()
                self.stream.seek(0, 2)
                if self.stream.tell() + len(msg.encode("utf-8", errors="replace")) >= self.maxBytes:
                    return 1
            except Exception:
                pass
        return 0


def cleanup_old_logs(log_dir, retention_days):
    path = Path(log_dir)
    if not path.exists():
        return
    cutoff = time.time() - retention_days * 86400
    for f in list(path.glob("*.log*")):
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


def setup_logging(config):
    log_cfg = config.get("logging", {})

    logfile = Path(log_cfg.get("logfile", config.get("logfile", "logs/qb_auto.log")))
    level_str = log_cfg.get("level", "DEBUG" if config.get("debug_mode") else "INFO")
    environment = log_cfg.get("environment", "production")
    json_format = log_cfg.get("json_format", environment == "production")

    rotation = log_cfg.get("rotation", {})
    max_bytes = rotation.get("max_bytes", 10 * 1024 * 1024)
    backup_count = rotation.get("backup_count", 30)
    retention_days = rotation.get("retention_days", 30)
    when = rotation.get("when", "midnight")

    sensitive_masking = log_cfg.get("sensitive_masking", environment == "production")

    logfile.parent.mkdir(parents=True, exist_ok=True)

    level = _resolve_level(level_str)

    formatter = StructuredFormatter(environment=environment, json_format=json_format)
    sensitive_filter = SensitiveDataFilter(enabled=sensitive_masking)
    context_filter = ContextFilter()

    handlers = []

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.addFilter(sensitive_filter)
    console.addFilter(context_filter)
    handlers.append(console)

    file_handler = SizeTimeRotatingFileHandler(
        filename=logfile,
        maxBytes=max_bytes,
        when=when,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(sensitive_filter)
    file_handler.addFilter(context_filter)
    handlers.append(file_handler)

    logging.basicConfig(level=level, handlers=handlers, force=True)

    logging.getLogger("urllib3.connectionpool").setLevel(logging.INFO)
    logging.getLogger("qbittorrentapi").setLevel(logging.WARNING)

    cleanup_old_logs(str(logfile.parent), retention_days)


def _resolve_level(level_str):
    if level_str.upper() == "FATAL":
        return FATAL
    return getattr(logging, level_str.upper(), logging.INFO)


def get_logger(name):
    return logging.getLogger(name)
