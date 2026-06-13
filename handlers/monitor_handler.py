from client import QBittorrentClient
from logger import get_logger


class MonitorHandler:
    def __init__(
        self,
        client: QBittorrentClient,
        stall_timeout_seconds: float,
        demotion_threshold: int,
    ):
        self.client = client
        self.stall_timeout = stall_timeout_seconds
        self.demotion_threshold = demotion_threshold
        self._tracker: dict[str, tuple[float, float]] = {}  # hash -> (first_seen, last_progress)
        self.logger = get_logger(self.__class__.__name__)

    def handle(self, batch: dict):
        torrents = batch["torrents"]
        downloading_count = batch["downloading_count"]
        now = batch["now"]

        active = {t.hash for t in torrents}
        for t in torrents:
            progress = t.progress if t.progress is not None else 0.0
            if t.hash in self._tracker:
                _first_seen, last_progress = self._tracker[t.hash]
                if progress > last_progress + 1e-6:
                    self._tracker[t.hash] = (now, progress)
            else:
                self._tracker[t.hash] = (now, progress)
        for h in list(self._tracker):
            if h not in active:
                del self._tracker[h]

        if downloading_count <= self.demotion_threshold:
            return

        to_demote = [
            t for t in torrents
            if (now - self._tracker.get(t.hash, (now, 0))[0]) > self.stall_timeout
        ]

        if to_demote:
            self.client.move_to_bottom(hashes=[t.hash for t in to_demote])
            for t in to_demote:
                self.logger.info(
                    "Demoted %s (%s) | state=%s | stalled_for=%.0fs",
                    t.hash[:8],
                    t.name,
                    t.state,
                    now - self._tracker[t.hash][0],
                )
