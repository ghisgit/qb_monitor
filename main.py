import os
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

    _validate_organize(data)


def _validate_organize(data: dict) -> None:
    """organize 为可选节；enabled=true 时校验全部依赖项（AI SDK、目录、凭证）。"""
    organize = data.get("organize")
    if not organize:
        return
    if not isinstance(organize, dict):
        raise ValueError("organize must be a mapping")

    enabled = organize.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError("organize.enabled must be a boolean")
    if not enabled:
        return

    tags = organize.get("tags")
    if not isinstance(tags, list) or not tags or not all(isinstance(t, str) and t.strip() for t in tags):
        raise ValueError("organize.tags must be a non-empty list of non-empty strings")

    library = organize.get("library")
    if not isinstance(library, dict):
        raise ValueError("organize.library must be a mapping")
    for key in ("movies_dir", "tv_dir", "fallback_dir"):
        value = library.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"organize.library.{key} must be a non-empty string")

    on_exists = organize.get("on_exists", "skip")
    if on_exists not in ("skip", "overwrite"):
        raise ValueError("organize.on_exists must be 'skip' or 'overwrite'")
    on_failure = organize.get("on_match_failure", "fallback")
    if on_failure not in ("fallback", "fail"):
        raise ValueError("organize.on_match_failure must be 'fallback' or 'fail'")

    min_size = organize.get("min_file_size_mb", 0)
    if not isinstance(min_size, (int, float)) or isinstance(min_size, bool) or min_size < 0:
        raise ValueError("organize.min_file_size_mb must be a non-negative number")

    for flag in ("include_episode_title", "include_tmdb_id"):
        if flag in organize and not isinstance(organize[flag], bool):
            raise ValueError(f"organize.{flag} must be a boolean")

    ext_list = organize.get("video_extensions")
    if ext_list is not None and (
        not isinstance(ext_list, list) or not ext_list or not all(isinstance(e, str) and e.strip() for e in ext_list)
    ):
        raise ValueError("organize.video_extensions must be a non-empty list of non-empty strings")

    tmdb_key = organize.get("tmdb_api_key")
    if not isinstance(tmdb_key, str) or not tmdb_key.strip():
        raise ValueError("organize.tmdb_api_key must be a non-empty string")

    ai_retries = organize.get("ai_retries", 1)
    if not isinstance(ai_retries, int) or isinstance(ai_retries, bool) or ai_retries < 0:
        raise ValueError("organize.ai_retries must be a non-negative integer")

    dsh = organize.get("dsh")
    if not isinstance(dsh, dict):
        raise ValueError("organize.dsh must be a mapping")

    request_timeout = dsh.get("request_timeout_seconds", 300)
    if not isinstance(request_timeout, (int, float)) or isinstance(request_timeout, bool) or request_timeout <= 0:
        raise ValueError("organize.dsh.request_timeout_seconds must be a positive number")

    api_key = dsh.get("api_key")
    if not (isinstance(api_key, str) and api_key.strip()) and not os.environ.get("DEEPSEEK_API_KEY"):
        raise ValueError("organize.dsh.api_key or environment variable DEEPSEEK_API_KEY is required")

    if "model" in dsh and (not isinstance(dsh["model"], str) or not dsh["model"].strip()):
        raise ValueError("organize.dsh.model must be a non-empty string")
    if "base_url" in dsh and dsh["base_url"] is not None and not isinstance(dsh["base_url"], str):
        raise ValueError("organize.dsh.base_url must be a string or omitted")
    if "language" in dsh and (not isinstance(dsh["language"], str) or not dsh["language"].strip()):
        raise ValueError("organize.dsh.language must be a non-empty string")
    if "session_root" in dsh and (not isinstance(dsh["session_root"], str) or not dsh["session_root"].strip()):
        raise ValueError("organize.dsh.session_root must be a non-empty string")


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

    organize_cfg = data.get("organize")
    matcher = None
    if organize_cfg and organize_cfg.get("enabled"):
        try:
            from ai_matcher import DeepSeekMatcher, MatcherConfig
            from handlers.organize_handler import OrganizeHandler
        except ImportError as e:
            raise ValueError(
                "organize 需要 deepseek-harness-sdk（无 Windows 平台运行时，请使用 Docker/Linux 或关闭 organize）"
            ) from e

        dsh_cfg = organize_cfg.get("dsh", {})
        matcher = DeepSeekMatcher(
            MatcherConfig(
                tmdb_api_key=organize_cfg["tmdb_api_key"],
                model=dsh_cfg.get("model", "deepseek-v4-flash"),
                api_key=dsh_cfg.get("api_key") or None,
                base_url=dsh_cfg.get("base_url") or None,
                language=dsh_cfg.get("language", "zh-CN"),
                request_timeout_seconds=dsh_cfg.get("request_timeout_seconds", 300),
                session_root=dsh_cfg.get("session_root", "sessions"),
                ai_retries=organize_cfg.get("ai_retries", 1),
            )
        )
        organize_handler = OrganizeHandler(client, matcher, organize_cfg)
        for tag in organize_cfg["tags"]:
            orchestrator.register_handler(tag, organize_handler.handle)
        logger.info(
            "Organize handler enabled | tags=%s | model=%s | movies=%s | tv=%s",
            organize_cfg["tags"],
            dsh_cfg.get("model", "deepseek-v4-flash"),
            organize_cfg["library"]["movies_dir"],
            organize_cfg["library"]["tv_dir"],
        )

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
        if matcher is not None:
            try:
                matcher.close()
            except Exception:
                logger.warning("Failed to close AI matcher", exc_info=True)


if __name__ == "__main__":
    main()
