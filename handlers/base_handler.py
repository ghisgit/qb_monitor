from abc import ABC, abstractmethod
from client import QBittorrentClient
from logger import get_logger, ContextFilter
from models import MatchRule


class BaseHandler(ABC):
    def __init__(self, client: QBittorrentClient, rules: list[MatchRule]):
        self.client = client
        self.rules = rules
        self.logger = get_logger(self.__class__.__name__)

    def _match_rule(self, name: str) -> MatchRule | None:
        for rule in self.rules:
            if rule.matches(name):
                self.logger.debug("Rule matched: '%s' on '%s'", rule.pattern, name)
                return rule
        return None

    def _cleanup_processing_tag(self, torrent_hash: str):
        short_hash = torrent_hash[:8]
        try:
            self.client.remove_torrents_tag(hashes=torrent_hash, tag="processing")
            self.logger.debug("Removed 'processing' tag from %s", short_hash)
        except Exception as e:
            self.logger.warning(
                "Failed to remove 'processing' tag from %s: %s", short_hash, e
            )

    @abstractmethod
    def handle(self, task):
        pass
