from abc import ABC, abstractmethod
from client import QBittorrentClient
from models import TorrentTask, MatchRule
from logger import setup_logger


class BaseHandler(ABC):
    def __init__(self, client: QBittorrentClient, rules: list[MatchRule]):
        self.client = client
        self.rules = rules
        self.logger = setup_logger(self.__class__.__name__)

    def _match_rule(self, name: str) -> MatchRule | None:
        """返回第一个匹配的规则，并记录调试信息"""
        for rule in self.rules:
            if rule._compiled.search(name):
                self.logger.debug(f"    🎯 Rule matched: '{rule.pattern}' on '{name}'")
                return rule
        return None

    def _cleanup_processing_tag(self, torrent_hash: str):
        """移除 processing 标签（无论成功失败都清理）"""
        short_hash = torrent_hash[:8]
        try:
            self.client.remove_torrents_tag(hashes=torrent_hash, tag="processing")
            self.logger.debug(f"    🏷️  Removed 'processing' tag from {short_hash}")
        except Exception as e:
            self.logger.warning(
                f"    ⚠️  Failed to remove 'processing' tag from {short_hash}: {e}"
            )

    @abstractmethod
    def handle(self, task: TorrentTask):
        pass
