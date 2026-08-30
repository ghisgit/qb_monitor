import queue
import re
import threading
import time

import yaml
from qbittorrentapi import TorrentDictionary

from _breaker import CircuitBreakerConfig, RetryConfig
from client import QBittorrentClient
from handlers.added_handler import AddedHandler
from handlers.category_tag_handler import CategoryTagHandler
from handlers.completed_handler import CompletedHandler
from handlers.monitor_handler import MonitorHandler
from logger import ContextFilter, get_logger, setup_logging
from models import MatchRule
from orchestrator import TorrentOrchestrator

MANDATORY_SECTIONS = {
    "qbittorrent": ["host", "username", "password"],
    "processor": [
        "poll_interval_seconds",
        "stall_timeout_hours",
        "max_worker_threads",
    ],
    "rules": ["added", "completed"],
    "logging": ["logfile"],
}


def load_config(path: str = "config.yaml") -> dict:
    with open(path, encoding="utf8") as f:
        data = yaml.safe_load(f)

    validate_config(data)
    return data


def validate_config(data: dict):
    missing = []
    for section, keys in MANDATORY_SECTIONS.items():
        if section not in data:
            missing.append(section)
            continue
        for key in keys:
            if key not in data[section]:
                missing.append(f"{section}.{key}")

    if missing:
        raise ValueError(f"Missing config keys: {', '.join(missing)}")

    poll = data["processor"]["poll_interval_seconds"]
    if not isinstance(poll, (int, float)) or poll <= 0:
        raise ValueError("processor.poll_interval_seconds must be a positive number")

    max_workers = data["processor"]["max_worker_threads"]
    if not isinstance(max_workers, int) or max_workers < 1:
        raise ValueError("processor.max_worker_threads must be a positive integer")

    category_tags = data.get("category_tags")
    if category_tags:
        if not isinstance(category_tags, dict):
            raise ValueError("category_tags must be a mapping of action ('added'/'completed') to tag rules")
        for action, mapping in category_tags.items():
            if action not in ("added", "completed"):
                raise ValueError(f"category_tags.{action}: action must be 'added' or 'completed'")
            if not isinstance(mapping, dict) or not mapping:
                raise ValueError(f"category_tags.{action} must be a non-empty mapping of regex pattern to tag(s)")
            for pattern, tags in mapping.items():
                if not isinstance(pattern, str) or not pattern.strip():
                    raise ValueError(f"category_tags.{action}: regex patterns must be non-empty strings")
                try:
                    re.compile(pattern)
                except re.error as e:
                    raise ValueError(f"category_tags.{action}.{pattern} is not a valid regex: {e}") from e
                values = [tags] if isinstance(tags, str) else tags
                if (
                    not isinstance(values, list)
                    or not values
                    or not all(isinstance(t, str) and t.strip() for t in values)
                ):
                    raise ValueError(
                        f"category_tags.{action}.{pattern} must be a non-empty string or list of non-empty strings"
                    )


def main():
    data = load_config()
    setup_logging(data)

    logger = get_logger("qb_monitor")

    client_cfg = data.get("client", {})
    client = QBittorrentClient(
        host=data["qbittorrent"]["host"],
        username=data["qbittorrent"]["username"],
        password=data["qbittorrent"]["password"],
        connect_timeout=client_cfg.get("connect_timeout", 5.0),
        read_timeout=client_cfg.get("read_timeout", 30.0),
        retry_cfg=RetryConfig(**client_cfg.get("retry", {})) if client_cfg.get("retry") else None,
        breaker_cfg=CircuitBreakerConfig(**client_cfg.get("circuit_breaker", {}))
        if client_cfg.get("circuit_breaker")
        else None,
    )

    try:
        client.setup_autorun()
    except Exception:
        logger.warning("Failed to configure autorun, continuing anyway", exc_info=True)

    task_queue = queue.Queue()

    added_handler = AddedHandler(client, [MatchRule(p) for p in data["rules"]["added"]])
    completed_handler = CompletedHandler(client, [MatchRule(p) for p in data["rules"]["completed"]])

    orchestrator = TorrentOrchestrator(
        client=client,
        task_queue=task_queue,
        poll_interval_seconds=data["processor"]["poll_interval_seconds"],
        stall_timeout_hours=data["processor"]["stall_timeout_hours"],
    )
    monitor_handler = MonitorHandler(
        client=client,
        stall_timeout_seconds=data["processor"]["stall_timeout_hours"] * 3600,
    )
    orchestrator.register_handler("added", added_handler.handle, enable_post_chain=True)
    orchestrator.register_handler("completed", completed_handler.handle, enable_post_chain=True)
    orchestrator.register_batch_handler("monitoring", monitor_handler.handle)

    category_tags = data.get("category_tags")
    if category_tags:
        category_tag_handler = CategoryTagHandler(client, category_tags)
        # post 处理器按触发标签限定作用域，避免 added 任务误用 completed 的映射
        if "added" in category_tags:
            orchestrator.register_post_handler(category_tag_handler.handle_added, tags="added")
        if "completed" in category_tags:
            orchestrator.register_post_handler(category_tag_handler.handle_completed, tags="completed")

    stop_event = threading.Event()

    def worker():
        while not stop_event.is_set():
            try:
                task = task_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                if isinstance(task, dict) and task.get("type") == "monitoring":
                    ContextFilter.set(operation="monitoring")
                elif isinstance(task, TorrentDictionary):
                    ContextFilter.set(
                        operation="task_dispatch",
                        torrent_hash=task.hash[:8],
                        torrent_name=task.name,
                    )
                orchestrator.dispatch(task)
            except Exception as e:
                if isinstance(task, dict):
                    logger.error("MonitorHandler error: %s", e, exc_info=True)
                else:
                    logger.error(
                        "Handler error for %s (%s): %s",
                        task.hash[:8],
                        task.name,
                        e,
                        exc_info=True,
                    )
            finally:
                ContextFilter.clear()
                task_queue.task_done()

    worker_threads = []
    for i in range(data["processor"]["max_worker_threads"]):
        t = threading.Thread(target=worker, daemon=True, name=f"TaskWorker-{i}")
        t.start()
        worker_threads.append(t)

    orch_thread = threading.Thread(target=orchestrator.run, daemon=True, name="Orchestrator")
    orch_thread.start()

    logger.info("🚀 All services started. Press Ctrl+C to exit.")
    try:
        while not stop_event.is_set():
            stop_event.wait(timeout=5.0)
            for t in worker_threads:
                if not t.is_alive():
                    logger.error("Worker thread %s died unexpectedly", t.name)
            if not orch_thread.is_alive():
                logger.error("Orchestrator thread died unexpectedly")
    except KeyboardInterrupt:
        logger.info("🛑 Shutting down...")
        orchestrator.stop()
        stop_event.set()
        deadline = time.monotonic() + 30
        while task_queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.1)
        if task_queue.unfinished_tasks:
            logger.warning("Shutdown timed out with %d tasks remaining", task_queue.unfinished_tasks)
        else:
            logger.info("✅ All tasks completed. Bye!")


if __name__ == "__main__":
    main()
