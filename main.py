import queue
import threading

import yaml
from qbittorrentapi import TorrentDictionary

from client import QBittorrentClient
from handlers.added_handler import AddedHandler
from handlers.completed_handler import CompletedHandler
from handlers.monitor_handler import MonitorHandler
from logger import ContextFilter, get_logger, setup_logging
from models import MatchRule
from orchestrator import TorrentOrchestrator

CONFIG_SCHEMA = {
    "qbittorrent": ["host", "username", "password"],
    "processor": [
        "poll_interval_seconds",
        "stall_timeout_minutes",
        "demotion_threshold",
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
    for section, keys in CONFIG_SCHEMA.items():
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


def main():
    data = load_config()
    setup_logging(data)

    logger = get_logger("qb_monitor")

    client = QBittorrentClient(
        host=data["qbittorrent"]["host"],
        username=data["qbittorrent"]["username"],
        password=data["qbittorrent"]["password"],
    )

    task_queue = queue.Queue()

    added_handler = AddedHandler(client, [MatchRule(p) for p in data["rules"]["added"]])
    completed_handler = CompletedHandler(client, [MatchRule(p) for p in data["rules"]["completed"]])

    orchestrator = TorrentOrchestrator(
        client=client,
        task_queue=task_queue,
        poll_interval_seconds=data["processor"]["poll_interval_seconds"],
        stall_timeout_minutes=data["processor"]["stall_timeout_minutes"],
        demotion_threshold=data["processor"]["demotion_threshold"],
    )
    monitor_handler = MonitorHandler(
        client=client,
        stall_timeout_seconds=data["processor"]["stall_timeout_minutes"] * 60,
        demotion_threshold=data["processor"]["demotion_threshold"],
    )
    orchestrator.register_handler("added", added_handler.handle)
    orchestrator.register_handler("completed", completed_handler.handle)
    orchestrator.register_handler("monitoring", monitor_handler.handle)

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
        stop_event.wait()
    except KeyboardInterrupt:
        logger.info("🛑 Shutting down...")
        orchestrator.stop()
        stop_event.set()
        task_queue.join()
        logger.info("✅ All tasks completed. Bye!")


if __name__ == "__main__":
    main()
