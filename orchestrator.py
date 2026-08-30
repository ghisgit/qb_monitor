import queue
import threading
import time
import uuid
from collections.abc import Callable
from datetime import timedelta

from qbittorrentapi import TorrentDictionary, TorrentInfoList

from client import QBittorrentClient
from logger import ContextFilter, get_logger

logger = get_logger(__name__)

TorrentHandler = Callable[[TorrentDictionary], None]
BatchHandler = Callable[[dict], None]


class TorrentOrchestrator:
    def __init__(
        self,
        client: QBittorrentClient,
        task_queue: queue.Queue,
        poll_interval_seconds: int = 30,
        stall_timeout_hours: float = 1.0,
    ):
        self.client = client
        self.task_queue = task_queue
        self.poll_interval = poll_interval_seconds
        self.stall_timeout = timedelta(hours=stall_timeout_hours)
        self._dispatcher: dict[str, TorrentHandler] = {}  # 触发标签 → 种子处理器（注册顺序即优先级）
        self._batch_dispatcher: dict[str, BatchHandler] = {}  # 批量任务名 → 处理器
        self._post_handlers: list[TorrentHandler] = []
        self._post_tags: set[str] = set()  # 启用 post 链的触发标签
        self._stop_event = threading.Event()

    def register_handler(self, tag: str, handler_fn: TorrentHandler, enable_post_chain: bool = False) -> None:
        """注册触发标签处理器；enable_post_chain=True 时该标签处理成功后会执行 post 链。"""
        self._dispatcher[tag] = handler_fn
        if enable_post_chain:
            self._post_tags.add(tag)

    def register_batch_handler(self, name: str, handler_fn: BatchHandler) -> None:
        """注册批量任务处理器（dict 任务按 type 字段路由）。"""
        self._batch_dispatcher[name] = handler_fn

    def register_post_handler(self, handler_fn: TorrentHandler) -> None:
        """注册 post 链处理器，按注册顺序依次执行，单点失败不影响后续。"""
        self._post_handlers.append(handler_fn)

    def dispatch(self, task):
        # TorrentDictionary 是 dict 子类，必须先判断种子任务再走批量路由
        if isinstance(task, TorrentDictionary):
            self._dispatch_torrent(task)
            return
        if isinstance(task, dict):
            self._dispatch_batch(task)
            return
        logger.warning("Unknown task type: %s", type(task).__name__)

    def _dispatch_torrent(self, task: TorrentDictionary) -> None:
        tag_set = set(task.tags.split(",")) if task.tags else set()
        # 注册顺序首个命中（种子同时带多个触发标签属异常态，仅入队处理一次）
        tag = next((t for t in self._dispatcher if t in tag_set), None)
        if tag is None:
            logger.warning("No handler for tags: %s", task.tags)
            return

        ContextFilter.set(operation=tag, torrent_hash=task.hash[:8], torrent_name=task.name)
        # 异常不捕获、向上抛给 worker 记日志：失败 → 不移除触发标签、不跑 post 链，下轮重入队重试
        self._dispatcher[tag](task)

        self._remove_trigger_tag(task, tag)

        if tag in self._post_tags:
            self._run_post_handlers(task)

    def _dispatch_batch(self, task: dict) -> None:
        name = task.get("type")
        if not isinstance(name, str):
            logger.warning("Batch task missing 'type' field: %s", task)
            return
        handler = self._batch_dispatcher.get(name)
        if handler is None:
            logger.warning("No batch handler for task type: %s", name)
            return
        ContextFilter.set(operation=name)
        handler(task)

    def _remove_trigger_tag(self, task: TorrentDictionary, tag: str) -> None:
        """移除触发标签；失败仅 warning（下轮重入队重试，动作幂等）。"""
        try:
            self.client.remove_torrents_tag(hashes=task.hash, tag=tag)
            logger.debug("Removed trigger tag '%s' from %s", tag, task.hash[:8])
        except Exception as e:
            logger.warning(
                "Failed to remove trigger tag '%s' from %s: %s (will retry next cycle)",
                tag,
                task.hash[:8],
                e,
            )

    def _run_post_handlers(self, task: TorrentDictionary) -> None:
        for post_fn in self._post_handlers:
            try:
                post_fn(task)
            except Exception as e:
                logger.error(
                    "Post handler %s failed for %s: %s",
                    getattr(post_fn, "__qualname__", repr(post_fn)),
                    task.hash[:8],
                    e,
                    exc_info=True,
                )

    def _process_torrents(self):
        cycle_id = uuid.uuid4().hex[:8]
        ContextFilter.set(request_id=cycle_id, operation="poll_cycle")

        try:
            logger.debug("Cycle %s: fetching torrents...", cycle_id)
            all_torrents: TorrentInfoList = self.client.get_torrents()
            logger.debug("Cycle %s: fetched %d torrents", cycle_id, len(all_torrents))

            # 触发标签动态推导自注册表：新增标签只需 register_handler，零改动轮询逻辑
            trigger_tags = set(self._dispatcher)
            tag_hit_counts: dict[str, int] = {}
            to_process: list[TorrentDictionary] = []
            monitoring_torrents: list[TorrentDictionary] = []
            downloading_count = 0
            now = time.time()

            for t in all_torrents:
                state = t.state
                is_metadata = t.has_metadata
                tag_set = set(t.tags.split(",")) if t.tags else set()

                # Count all downloading-type and collect for monitoring
                if state in ("downloading", "metaDL", "stalledDL", "forcedDL", "queuedDL"):
                    downloading_count += 1
                    if state != "queuedDL" and t.progress < 0.95:
                        monitoring_torrents.append(t)

                # Skip torrents without metadata or currently being processed
                if not is_metadata or "processing" in tag_set:
                    continue

                # 标签驱动统一入队：集合精确匹配（in 语义），不移除触发标签
                hits = tag_set & trigger_tags
                if hits:
                    to_process.append(t)
                    for tag in hits:
                        tag_hit_counts[tag] = tag_hit_counts.get(tag, 0) + 1

            hit_summary = ", ".join(f"{tag}={n}" for tag, n in tag_hit_counts.items()) or "none"
            logger.debug(
                "Cycle summary: total=%d | queued=%d (%s) | monitoring=%d | downloading_count=%d",
                len(all_torrents),
                len(to_process),
                hit_summary,
                len(monitoring_torrents),
                downloading_count,
            )

            if to_process:
                # 批量打 processing 防止下轮重复入队；触发标签由 dispatch 成功后统一移除
                self.client.add_torrents_tag(hashes=[t.hash for t in to_process], tags="processing")
                for t in to_process:
                    self.task_queue.put(t)
                logger.info("Queued %d torrents for processing | %s", len(to_process), hit_summary)

            if monitoring_torrents:
                demotion_threshold = self.client.get_max_active_downloads()
                logger.debug(
                    "Dispatching monitoring batch: %d torrents | downloading_count=%d | demotion_threshold=%d",
                    len(monitoring_torrents),
                    downloading_count,
                    demotion_threshold,
                )
                self.task_queue.put(
                    {
                        "type": "monitoring",
                        "torrents": monitoring_torrents,
                        "downloading_count": downloading_count,
                        "demotion_threshold": demotion_threshold,
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

            logger.info("Recovering %d orphaned 'processing' tasks...", len(all_torrents))
            self.client.remove_torrents_tag(hashes=[t.hash for t in all_torrents], tag="processing")

            added_torrents = []
            completed_torrents = []

            for t in all_torrents:
                if t.progress < 1:
                    added_torrents.append(t.hash)
                else:
                    completed_torrents.append(t.hash)

            if added_torrents:
                self.client.add_torrents_tag(hashes=added_torrents, tags="added")
                logger.info("Recovered %d 'added' tasks", len(added_torrents))
            if completed_torrents:
                self.client.add_torrents_tag(hashes=completed_torrents, tags="completed")
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
            "TorrentOrchestrator started | Interval: %ds | Stall timeout: %sh",
            self.poll_interval,
            self.stall_timeout.total_seconds() / 3600,
        )

        self._recover_processing_tasks()

        logger.debug("Entering main loop | poll_interval=%ds", self.poll_interval)
        while not self._stop_event.is_set():
            self._process_torrents()
            self._stop_event.wait(timeout=self.poll_interval)
