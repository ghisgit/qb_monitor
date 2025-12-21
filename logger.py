import sys
import logging
from pathlib import Path
from logging.handlers import RotatingFileHandler
from typing import Optional

_HANDLERS_INITIALIZED = False
_GLOBAL_DEBUG = False  # 全局 debug 状态


def setup_logger(
    name: str = "qb_auto",
    log_file: Optional[str] = "logs/qb_auto.log",
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 3,
    console: bool = True,
    debug: Optional[bool] = None,
) -> logging.Logger:
    global _HANDLERS_INITIALIZED, _GLOBAL_DEBUG

    # 如果显式指定了 debug，就更新全局状态（仅当是主 logger 或首次设置）
    if debug is not None:
        _GLOBAL_DEBUG = debug
        effective_debug = debug
    else:
        effective_debug = _GLOBAL_DEBUG

    # 构建带命名空间的 logger 名称
    if name == "qb_auto":
        full_name = "qb_auto"
    else:
        full_name = f"qb_auto.{name}" if not name.startswith("qb_auto.") else name

    logger = logging.getLogger(full_name)
    level = logging.DEBUG if effective_debug else logging.INFO
    logger.setLevel(level)

    # 仅为主 logger 初始化 handlers（一次）
    if full_name == "qb_auto" and not _HANDLERS_INITIALIZED:
        _setup_handlers(logger, log_file, max_bytes, backup_count, console)
        _HANDLERS_INITIALIZED = True
        logger.propagate = False  # 阻止向 root 传播

    return logger


def _setup_handlers(logger, log_file, max_bytes, backup_count, console):
    formatter = logging.Formatter(
        fmt="%(asctime)s.%(msecs)03d | %(levelname)-8s | %(name)15s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if console:
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(formatter)
        logger.addHandler(ch)

    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            log_file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )
        fh.setFormatter(formatter)
        logger.addHandler(fh)
