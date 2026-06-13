import queue
import threading
import time
import uuid
from datetime import timedelta
from typing import Callable

from qbittorrentapi import TorrentInfoList, TorrentDictionary

from client import QBittorrentClient
from logger import get_logger, ContextFilter

logger = get_logger(__name__)


class TorrentOrchestrator:
    def __init__(
        self,
        client: QBittorrentClient,
        task_queue: queue.Queue,
        poll_interval_seconds: int = 30,
        stall_timeout_minutes: int = 30,
        demotion_threshold: int = 30,
    ):
        self.client = client
        self.task_queue = task_queue
        self.poll_interval = poll_interval_seconds
        self.stall_timeout = timedelta(minutes=stall_timeout_minutes)
        self.demotion_threshold = demotion_threshold
        self._dispatcher: dict[str, Callable] = {}
        self._stop_event = threading.Event()

    def register_handler(self, tag: str, handler_fn: Callable):
        self._dispatcher[tag] = handler_fn

    def dispatch(self, task):
        if isinstance(task, dict) and task.get("type") == "monitoring":
            self._dispatcher["monitoring"](task)
            return
        for tag, handler_fn in self._dispatcher.items():
            if tag in task.tags:
                ContextFilter.set(
                    operation=tag, torrent_hash=task.hash[:8], torrent_name=task.name
                )
                handler_fn(task)
                return
        logger.warning("No handler for tags: %s", task.tags)

    def _process_torrents(self):
        cycle_id = uuid.uuid4().hex[:8]
        ContextFilter.set(request_id=cycle_id, operation="poll_cycle")

        try:
            logger.debug("Cycle %s: fetching torrents...", cycle_id)
            all_torrents: TorrentInfoList = self.client.get_torrents()
            logger.debug("Cycle %s: fetched %d torrents", cycle_id, len(all_torrents))

            added_torrents: list[TorrentDictionary] = []
            completed_torrents: list[TorrentDictionary] = []
            monitoring_torrents: list[TorrentDictionary] = []
            downloading_count = 0
            now = time.time()

            for t in all_torrents:
                state = t.state
                is_metadata = t.has_metadata
                tags = t.tags

                # Count all downloading-type and collect for monitoring
                if state in ("downloading", "metaDL", "stalledDL", "forcedDL", "queuedDL"):
                    downloading_count += 1
                    if state != "queuedDL" and t.progress < 0.95:
                        monitoring_torrents.append(t)

                # Skip torrents without metadata or currently being processed
                if not is_metadata or "processing" in tags:
                    continue

                # Tag-based classification for handler processing
                if "added" in tags:
                    added_torrents.append(t)
                elif "completed" in tags:
                    completed_torrents.append(t)

            logger.debug(
                "Cycle summary: total=%d | added=%d | completed=%d | monitoring=%d | downloading_count=%d",
                len(all_torrents), len(added_torrents), len(completed_torrents),
                len(monitoring_torrents), downloading_count,
            )

            if added_torrents:
                added_hashes = [t.hash for t in added_torrents]
                self.client.add_torrents_tag(hashes=added_hashes, tag="processing")

                for t in added_torrents:
                    self.task_queue.put(t)

                self.client.remove_torrents_tag(hashes=added_hashes, tag="added")
                logger.info(
                    "Queued %d added torrents for processing", len(added_torrents)
                )

            if completed_torrents:
                completed_hashes = [t.hash for t in completed_torrents]
                self.client.add_torrents_tag(hashes=completed_hashes, tag="processing")

                for t in completed_torrents:
                    self.task_queue.put(t)

                self.client.remove_torrents_tag(
                    hashes=completed_hashes, tag="completed"
                )
                logger.info(
                    "Queued %d completed torrents for processing",
                    len(completed_torrents),
                )

            if monitoring_torrents:
                logger.debug(
                    "Dispatching monitoring batch: %d torrents | downloading_count=%d",
                    len(monitoring_torrents), downloading_count,
                )
                self.task_queue.put(
                    {
                        "type": "monitoring",
                        "torrents": monitoring_torrents,
                        "downloading_count": downloading_count,
                        "now": now,
                    }
                )

        except Exception as e:
            logger.error("Error in orchestration cycle: %s", e, exc_info=True)
        finally:
            ContextFilter.clear()

    def _recover_processing_tasks(self) -> None:
        ContextFilter.set(operation="recovery")
        try:
            all_torrents = self.client.get_torrents(tag="processing")

            if not all_torrents:
                logger.debug("No orphaned 'processing' tasks found at startup.")
                return

            logger.info(
                "Recovering %d orphaned 'processing' tasks...", len(all_torrents)
            )
            self.client.remove_torrents_tag(
                hashes=[t.hash for t in all_torrents], tag="processing"
            )

            added_torrents = []
            completed_torrents = []

            for t in all_torrents:
                if t.progress < 1:
                    added_torrents.append(t.hash)
                else:
                    completed_torrents.append(t.hash)

            if added_torrents:
                self.client.add_torrents_tag(hashes=added_torrents, tag="added")
                logger.info("Recovered %d 'added' tasks", len(added_torrents))
            if completed_torrents:
                self.client.add_torrents_tag(hashes=completed_torrents, tag="completed")
                logger.info("Recovered %d 'completed' tasks", len(completed_torrents))

            logger.info("Orphaned 'processing' tags cleaned up.")

        except Exception as e:
            logger.error("Failed to recover processing tasks: %s", e)
        finally:
            ContextFilter.clear()

    def stop(self):
        self._stop_event.set()

    def run(self):
        logger.info(
            "TorrentOrchestrator started | Interval: %ss | Stall timeout: %sm | Demotion threshold: %d",
            self.poll_interval,
            self.stall_timeout.total_seconds() / 60,
            self.demotion_threshold,
        )

        self._recover_processing_tasks()

        logger.debug("Entering main loop | poll_interval=%ds", self.poll_interval)
        while not self._stop_event.is_set():
            self._process_torrents()
            self._stop_event.wait(timeout=self.poll_interval)
