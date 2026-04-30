import queue
import logging
import threading
from datetime import timedelta
from typing import Callable

from qbittorrentapi import TorrentInfoList, TorrentDictionary

from client import QBittorrentClient

logger = logging.getLogger(__name__)


class TorrentOrchestrator:
    def __init__(
        self,
        client: QBittorrentClient,
        task_queue: queue.Queue,
        poll_interval_seconds: int = 30,
        stall_timeout_minutes: int = 30,
    ):
        self.client = client
        self.task_queue = task_queue
        self.poll_interval = poll_interval_seconds
        self.stall_timeout = timedelta(minutes=stall_timeout_minutes)
        self._dispatcher: dict[str, Callable] = {}
        self._stop_event = threading.Event()

    def register_handler(self, tag: str, handler_fn: Callable):
        self._dispatcher[tag] = handler_fn

    def dispatch(self, task):
        for tag, handler_fn in self._dispatcher.items():
            if tag in task.tags:
                handler_fn(task)
                return
        logger.warning(f"No handler for tags: {task.tags}")

    def _process_torrents(self):
        try:
            all_torrents: TorrentInfoList = self.client.get_torrents()

            added_torrents: list[TorrentDictionary] = []
            completed_torrents: list[TorrentDictionary] = []
            stalled_torrents: list[TorrentDictionary] = []
            downloading_count = 0

            for t in all_torrents:
                state = t.state
                is_metadata = t.has_metadata
                tags = t.tags

                if not is_metadata or "processing" in tags:
                    if state == "metaDL":
                        stalled_torrents.append(t)
                    continue

                if "added" in tags:
                    added_torrents.append(t)
                elif "completed" in tags:
                    completed_torrents.append(t)

                if state in ("downloading", "metaDL", "stalledDL", "forcedDL"):
                    downloading_count += 1

            if added_torrents:
                added_hashes = [t.hash for t in added_torrents]
                self.client.add_torrents_tag(hashes=added_hashes, tag="processing")

                for t in added_torrents:
                    self.task_queue.put(t)

                self.client.remove_torrents_tag(hashes=added_hashes, tag="added")

            if completed_torrents:
                completed_hashes = [t.hash for t in completed_torrents]
                self.client.add_torrents_tag(hashes=completed_hashes, tag="processing")

                for t in completed_torrents:
                    self.task_queue.put(t)

                self.client.remove_torrents_tag(
                    hashes=completed_hashes, tag="completed"
                )

            if stalled_torrents and downloading_count > 200:
                hashes_to_demote = []

                for t in stalled_torrents:
                    if timedelta(seconds=t.time_active) > self.stall_timeout:
                        hashes_to_demote.append(t.hash)

                if hashes_to_demote:
                    self.client.move_to_bottom(hashes=hashes_to_demote)
                    logger.info(f"✅ Demoted {len(hashes_to_demote)} torrents")

        except Exception as e:
            logger.error(f"💥 Error in orchestration cycle: {e}", exc_info=True)

    def _recover_processing_tasks(self) -> None:
        try:
            all_torrents = self.client.get_torrents(tag="processing")

            if not all_torrents:
                logger.debug("→ No orphaned 'processing' tasks found at startup.")
                return

            logger.info(
                f"🔄 Recovering {len(all_torrents)} orphaned 'processing' tasks..."
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
                logger.info(f"🔄 Recovering {len(added_torrents)} 'added' tasks...")
            if completed_torrents:
                self.client.add_torrents_tag(hashes=completed_torrents, tag="completed")
                logger.info(
                    f"🔄 Recovering {len(completed_torrents)} 'completed' tasks..."
                )

            logger.info("✅ Orphaned 'processing' tags cleaned up.")

        except Exception as e:
            logger.error(f"⚠️ Failed to recover processing tasks: {e}")

    def stop(self):
        self._stop_event.set()

    def run(self):
        logger.info(
            f"🔄 TorrentOrchestrator started | "
            f"Interval: {self.poll_interval}s | "
            f"Stall timeout: {self.stall_timeout.total_seconds()/60}m"
        )

        self._recover_processing_tasks()

        while not self._stop_event.is_set():
            self._process_torrents()
            self._stop_event.wait(timeout=self.poll_interval)
