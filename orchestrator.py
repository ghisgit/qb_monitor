import time
import queue
import logging
from datetime import datetime, timedelta
from typing import Dict, Tuple

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
            all_torrents: TorrentInfoList = self.client.get_torrents()

            now = datetime.now()

            added_torrents: list[TorrentDictionary] = []
            completed_torrents: list[TorrentDictionary] = []
            stalled_torrents: list[TorrentDictionary] = []

            for t in all_torrents:
                # 状态
                state = t.state
                # 进度
                prog = t.progress
                # 是否在下载元数据
                is_metadata = t.has_metadata
                # 标签
                tags = t.tags

                if state == "metaDL" and is_metadata:
                    stalled_torrents.append(t)
                if not is_metadata or "processing" in tags:
                    continue
                if "added" in tags:
                    added_torrents.append(t)
                elif "completed" in tags:
                    completed_torrents.append(t)

            # 处理 added
            if added_torrents:
                added_hashes = [t.hash for t in added_torrents]
                self.client.add_torrents_tag(hashes=added_hashes, tag="processing")

                for t in added_torrents:
                    self.task_queue.put(t)

                self.client.remove_torrents_tag(hashes=added_hashes, tag="added")

            # 处理 completed
            if completed_torrents:
                completed_hashes = [t.hash for t in completed_torrents]
                self.client.add_torrents_tag(hashes=completed_hashes, tag="processing")

                for t in completed_torrents:
                    self.task_queue.put(t)

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
                    name = t.name

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
        移除 'processing' 标签，并根据下载进度添加对应的标签，让它们能被重新处理。
        """
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
