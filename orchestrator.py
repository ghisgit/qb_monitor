import time
import queue
import logging
from datetime import datetime, timedelta
from typing import Dict, Tuple

from client import QBittorrentClient
from models import TorrentTask, TorrentFile


logger = logging.getLogger(__name__)


class TorrentOrchestrator:
    def __init__(
        self,
        client: QBittorrentClient,
        task_queue: queue.Queue,
        poll_interval_seconds: int = 30,
        stall_timeout_minutes: int = 30,
        skip_patterns=None,
    ):
        self.client = client
        self.task_queue = task_queue
        self.poll_interval = poll_interval_seconds
        self.stall_timeout = timedelta(minutes=stall_timeout_minutes)
        self.skip_patterns = skip_patterns or []

        # StallMonitor 状态缓存
        self._progress_cache: Dict[str, Tuple[float, datetime]] = {}

    def _process_torrents(self):
        try:
            all_torrents = self.client.get_torrents()

            now = datetime.now()

            added_torrents = []
            completed_torrents = []
            stalled_torrents = []

            for t in all_torrents:
                if t.state in ("metaDL", "stalledDL") and t.progress < 0.95:
                    stalled_torrents.append(t)
                if not t.has_metadata or "processing" in t.tags:
                    continue
                if "added" in t.tags:
                    added_torrents.append(t)
                elif "completed" in t.tags:
                    completed_torrents.append(t)

            # 处理 added
            if added_torrents:
                added_hashes = [t.hash for t in added_torrents]
                self.client.add_torrents_tag(hashes=added_hashes, tag="processing")

                for t in added_torrents:
                    try:
                        files = self.client.get_torrent_files(hash=t.hash)
                        task = TorrentTask(
                            hash=t.hash,
                            name=t.name,
                            tag="added",
                            content_path=getattr(t, "content_path", ""),
                            files=[
                                TorrentFile(id=f.id, name=f.name, priority=f.priority)
                                for f in files
                            ],
                        )
                        self.task_queue.put(task)
                        logger.debug(f"→ Queued 'added' task for '{t.name}'")
                    except Exception as e:
                        logger.error(f"❌ Failed to load files for {t.hash[:8]}: {e}")

                self.client.remove_torrents_tag(hashes=added_hashes, tag="added")

            # 处理 completed
            if completed_torrents:
                completed_hashes = [t.hash for t in completed_torrents]
                self.client.add_torrents_tag(hashes=completed_hashes, tag="processing")

                for t in completed_torrents:
                    task = TorrentTask(
                        hash=t.hash,
                        name=t.name,
                        tag="completed",
                        content_path=getattr(t, "content_path", ""),
                    )
                    self.task_queue.put(task)

                self.client.remove_torrents_tag(
                    hashes=completed_hashes, tag="completed"
                )

            # 监控卡顿种子
            if stalled_torrents:
                current_hash_set = {t.hash for t in stalled_torrents}
                hashes_to_demote = []

                for t in stalled_torrents:
                    h = t.hash
                    prog = t.progress
                    name = getattr(t, "name", "<unknown>")

                    if h not in self._progress_cache:
                        self._progress_cache[h] = (prog, now)
                        continue

                    last_prog, last_active = self._progress_cache[h]
                    if prog > last_prog + 1e-6:
                        self._progress_cache[h] = (prog, now)
                    else:
                        stalled_duration = now - last_active
                        if stalled_duration >= self.stall_timeout:
                            if h not in hashes_to_demote:
                                hashes_to_demote.append(h)
                                logger.info(
                                    f"⏳ Demoting '{name}' ({h[:8]}): "
                                    f"{prog:.2%} stalled for {stalled_duration.total_seconds()/60:.1f}m"
                                )

                # 执行降级
                if hashes_to_demote:
                    self.client.move_to_bottom(hashes=hashes_to_demote)
                    logger.info(f"✅ Demoted {len(hashes_to_demote)} torrents")

                # 清理缓存
                stale_keys = set(self._progress_cache.keys()) - current_hash_set
                for h in stale_keys:
                    del self._progress_cache[h]

        except Exception as e:
            logger.error(f"💥 Error in orchestration cycle: {e}", exc_info=True)

    def _recover_processing_tasks(self) -> None:
        """
        启动时恢复卡在 'processing' 状态的种子。
        移除 'processing' 标签，让它们能被重新处理。
        """
        try:
            all_torrents = self.client.get_torrents()

            processing_hashes = [
                t.hash
                for t in all_torrents
                if "processing" in t.tags and t.has_metadata  # 排除 metaDL
            ]

            if not processing_hashes:
                logger.debug("→ No orphaned 'processing' tasks found at startup.")
                return

            logger.info(
                f"🔄 Recovering {len(processing_hashes)} orphaned 'processing' tasks..."
            )
            self.client.remove_torrents_tag(hashes=processing_hashes, tag="processing")
            logger.info("✅ Orphaned 'processing' tags cleaned up.")

        except Exception as e:
            logger.error(f"⚠️ Failed to recover processing tasks: {e}")

    def run(self):
        logger.info(
            f"🔄 TorrentOrchestrator started | "
            f"Interval: {self.poll_interval}s | "
            f"Stall timeout: {self.stall_timeout.total_seconds()/60}m"
        )

        self._recover_processing_tasks()

        while True:
            self._process_torrents()
            time.sleep(self.poll_interval)
