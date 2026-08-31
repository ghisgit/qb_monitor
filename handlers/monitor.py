"""监控处理器：追踪活跃下载种子的停滞，超时降级到队列底部。

日志行为（按需求重建）：
- 不再对每轮 poll 每个种子刷 debug 日志；
- 只记录「状态变化」事件：新跟踪 / 停止跟踪 / 开始停滞 / 停滞恢复 / 降级；
- 每轮只输出一个聚合摘要行（DEBUG），且**仅在确实有变化时**才输出，无变化则整轮静默。
"""

import threading
from dataclasses import dataclass

from core.client import QBittorrentClient
from core.logger import get_logger


@dataclass
class _TrackState:
    first_seen: float  # 首次跟踪 / 上次进度推进的时间
    last_progress: float  # 上次记录的进度
    stalled: bool = False  # 当前是否已判定为停滞（用于只在跨过阈值那刻触发一次事件）


class MonitorHandler:
    def __init__(self, client: QBittorrentClient, stall_timeout_seconds: float):
        self.client = client
        self.stall_timeout = stall_timeout_seconds
        self._lock = threading.Lock()
        self._tracker: dict[str, _TrackState] = {}
        self.logger = get_logger(self.__class__.__name__)

    def handle(self, batch: dict):
        torrents = batch["torrents"]
        downloading_count = batch["downloading_count"]
        demotion_threshold = batch["demotion_threshold"]
        now = batch["now"]

        active = {t.hash for t in torrents}
        newly_tracked: list = []
        recovered: list = []
        stalled_now: list = []
        removed_hashes: list[str] = []

        with self._lock:
            for t in torrents:
                progress = t.progress if t.progress is not None else 0.0
                state = self._tracker.get(t.hash)
                if state is None:
                    state = _TrackState(first_seen=now, last_progress=progress)
                    self._tracker[t.hash] = state
                    newly_tracked.append(t)
                elif progress > state.last_progress + 1e-6:
                    # 有进度推进：中止停滞。若此前处于停滞，视为「恢复」一次。
                    if state.stalled:
                        recovered.append(t)
                    state.first_seen = now
                    state.last_progress = progress
                    state.stalled = False

            # 不再处于活跃监控集合：停止跟踪
            for h in list(self._tracker):
                if h not in active:
                    removed_hashes.append(h)
                    del self._tracker[h]

            # 停滞判定：只在「跨过停滞阈值」那刻计一次，避免每轮重复告警
            for t in torrents:
                state = self._tracker.get(t.hash)
                if state is None:
                    continue
                if not state.stalled and (now - state.first_seen) > self.stall_timeout:
                    state.stalled = True
                    stalled_now.append(t)

        # 降级：仅当下载数超过活跃阈值时才真正执行（原始逻辑保持不变）
        demoted: list = []
        if downloading_count > demotion_threshold:
            with self._lock:
                to_demote = [
                    (t, self._tracker[t.hash].first_seen)
                    for t in torrents
                    if (now - self._tracker[t.hash].first_seen) > self.stall_timeout
                ]
            if to_demote:
                self.client.move_to_bottom(hashes=[t.hash for t, _fs in to_demote])
                demoted = to_demote

        for t in newly_tracked:
            self.logger.debug("Tracking %s (%s)", t.hash[:8], t.name)
        for h in removed_hashes:
            self.logger.debug("Stop tracking %s (no longer active)", h[:8])
        for t in recovered:
            self.logger.info("Recovered %s (%s) | progress=%.2f", t.hash[:8], t.name, t.progress or 0.0)
        for t in stalled_now:
            state = self._tracker[t.hash]
            self.logger.warning(
                "Stalled %s (%s) | state=%s | progress=%.2f | idle_for=%.0fs",
                t.hash[:8],
                t.name,
                t.state,
                t.progress or 0.0,
                now - state.first_seen,
            )
        for t, first_seen in demoted:
            self.logger.info(
                "Demoted %s (%s) | state=%s | stalled_for=%.0fs",
                t.hash[:8],
                t.name,
                t.state,
                now - first_seen,
            )

        self._emit_summary(
            now=now,
            newly_tracked=len(newly_tracked),
            stalled=len(stalled_now),
            recovered=len(recovered),
            removed=len(removed_hashes),
            demoted=len(demoted),
            downloading_count=downloading_count,
            demotion_threshold=demotion_threshold,
        )

    def _emit_summary(self, **counts) -> None:
        """每轮一个聚合摘要；没有任何变化时不输出（整轮静默）。"""
        changed = counts["newly_tracked"] > 0 or counts["stalled"] > 0 or counts["recovered"] > 0
        changed = changed or counts["removed"] > 0 or counts["demoted"] > 0
        if not changed:
            return
        self.logger.debug(
            "monitor: tracked=%d | new=%d | stalled=%d | recovered=%d | stopped=%d | demoted=%d | downloading=%d/%d",
            len(self._tracker),
            counts["newly_tracked"],
            counts["stalled"],
            counts["recovered"],
            counts["removed"],
            counts["demoted"],
            counts["downloading_count"],
            counts["demotion_threshold"],
        )
