import sys
import logging
import threading
import queue
import time
import yaml
from pathlib import Path
from logging.handlers import RotatingFileHandler

from models import MatchRule
from client import QBittorrentClient
from orchestrator import TorrentOrchestrator
from handlers.added_handler import AddedHandler
from handlers.completed_handler import CompletedHandler


with open("config.yaml") as f:
    data = yaml.safe_load(f)

log_filename = Path(data["logfile"])
log_filename.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.DEBUG if data["debug_mode"] else logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        RotatingFileHandler(
            filename=log_filename,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        ),
    ],
    force=True,
)


def main():
    logger = logging.getLogger("qb_monitor")

    client = QBittorrentClient(
        host=data["qbittorrent"]["host"],
        username=data["qbittorrent"]["username"],
        password=data["qbittorrent"]["password"],
    )

    task_queue = queue.Queue()

    # Handlers
    added_handler = AddedHandler(client, [MatchRule(p) for p in data["rules"]["added"]])
    completed_handler = CompletedHandler(
        client, [MatchRule(p) for p in data["rules"]["completed"]]
    )

    # Worker thread (consumer)
    def worker():
        while True:
            task = task_queue.get()  # 阻塞等待任务
            try:
                if task.tag == "added":
                    added_handler.handle(task)
                elif task.tag == "completed":
                    completed_handler.handle(task)
            except Exception as e:
                logger.error(
                    f"Handler error for {task.hash[:8]} ({task.name}): {e}",
                    exc_info=True,
                )
            finally:
                task_queue.task_done()

    # Start threads
    for i in range(data["processor"]["max_worker_threads"]):
        threading.Thread(target=worker, daemon=True, name=f"TaskWorker-{i}").start()

    orchestrator = TorrentOrchestrator(
        client=client,
        task_queue=task_queue,
        poll_interval_seconds=data["processor"]["poll_interval_seconds"],
        stall_timeout_minutes=data["processor"]["stall_timeout_minutes"],
    )
    threading.Thread(target=orchestrator.run, daemon=True, name="Orchestrator").start()

    logger.info("🚀 All services started. Press Ctrl+C to exit.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("🛑 Shutting down...")


if __name__ == "__main__":
    main()
