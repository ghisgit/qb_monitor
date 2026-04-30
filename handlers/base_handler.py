import logging
from abc import ABC, abstractmethod
from client import QBittorrentClient
from models import MatchRule


class BaseHandler(ABC):
    def __init__(self, client: QBittorrentClient, rules: list[MatchRule]):
        self.client = client
        self.rules = rules
        self.logger = logging.getLogger(self.__class__.__name__)

    def _match_rule(self, name: str) -> MatchRule | None:
        for rule in self.rules:
            if rule.matches(name):
                self.logger.debug(f"    🎯 Rule matched: '{rule.pattern}' on '{name}'")
                return rule
        return None

    def _cleanup_processing_tag(self, torrent_hash: str):
        short_hash = torrent_hash[:8]
        try:
            self.client.remove_torrents_tag(hashes=torrent_hash, tag="processing")
            self.logger.debug(f"    🏷️  Removed 'processing' tag from {short_hash}")
        except Exception as e:
            self.logger.warning(
                f"    ⚠️  Failed to remove 'processing' tag from {short_hash}: {e}"
            )

    @abstractmethod
    def handle(self, task):
        pass
