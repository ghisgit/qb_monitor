import threading

from client import QBittorrentClient
from logger import get_logger


class MonitorHandler:
    def __init__(self, client: QBittorrentClient, stall_timeout_seconds: float):
        self.client = client
        self.stall_timeout = stall_timeout_seconds
        self._lock = threading.Lock()
        self._tracker: dict[str, tuple[float, float]] = {}
        self.logger = get_logger(self.__class__.__name__)

    def handle(self, batch: dict):
        torrents = batch["torrents"]
        downloading_count = batch["downloading_count"]
        demotion_threshold = batch["demotion_threshold"]
        now = batch["now"]

        active = {t.hash for t in torrents}
        with self._lock:
            for t in torrents:
                progress = t.progress if t.progress is not None else 0.0
                if t.hash in self._tracker:
                    _first_seen, last_progress = self._tracker[t.hash]
                    if progress > last_progress + 1e-6:
                        self.logger.debug(
                            "Stall timer reset for %s: progress %.2f → %.2f", t.hash[:8], last_progress, progress
                        )
                        self._tracker[t.hash] = (now, progress)
                else:
                    self.logger.debug("Tracking %s (%s)", t.hash[:8], t.name)
                    self._tracker[t.hash] = (now, progress)
            for h in list(self._tracker):
                if h not in active:
                    self.logger.debug("Removed %s from tracker (no longer active)", h[:8])
                    del self._tracker[h]

            if downloading_count <= demotion_threshold:
                return

            to_demote = [t for t in torrents if (now - self._tracker.get(t.hash, (now, 0))[0]) > self.stall_timeout]

        if to_demote:
            self.client.move_to_bottom(hashes=[t.hash for t in to_demote])
            for t in to_demote:
                with self._lock:
                    first_seen = self._tracker.get(t.hash, (now, 0))[0]
                self.logger.info(
                    "Demoted %s (%s) | state=%s | stalled_for=%.0fs",
                    t.hash[:8],
                    t.name,
                    t.state,
                    now - first_seen,
                )
