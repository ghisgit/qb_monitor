import re

from qbittorrentapi import TorrentDictionary

from client import QBittorrentClient
from logger import get_logger

CategoryTagMapping = dict[re.Pattern[str], list[str]]


class CategoryTagHandler:
    """动作完成后按 qBittorrent 分类（Category）补打标签，added / completed 各自独立映射。

    qBittorrent 中每个种子最多只有一个分类；分类检测使用正则（re.search，
    不区分大小写）：单个分类可命中多个键，全部命中的标签合并应用（去重），
    正则同时提供了子分类级联能力（如 '^movies' 命中 movies/4K）。
    """

    def __init__(self, client: QBittorrentClient, mappings: dict[str, dict[str, str | list[str]]]):
        self.client = client
        self.logger = get_logger(self.__class__.__name__)
        # 按动作归一化：键编译为正则，值统一为 list[str]
        self.mappings: dict[str, CategoryTagMapping] = {
            action: {
                re.compile(pattern, re.IGNORECASE): [tags] if isinstance(tags, str) else list(tags)
                for pattern, tags in mapping.items()
            }
            for action, mapping in mappings.items()
        }

    def handle_added(self, task: TorrentDictionary) -> None:
        self._apply("added", task)

    def handle_completed(self, task: TorrentDictionary) -> None:
        self._apply("completed", task)

    def _apply(self, action: str, task: TorrentDictionary) -> None:
        mapping = self.mappings.get(action)
        if not mapping:
            return

        raw_category = task.get("category")  # get() 兼容缺失字段，避免 AttributeError
        category = raw_category.strip() if isinstance(raw_category, str) else ""
        if not category:
            self.logger.debug("No category set for %s, skipping %s tags", task.hash[:8], action)
            return

        matched: list[str] = []
        for pattern, tags in mapping.items():
            if pattern.search(category):
                self.logger.debug("Category '%s' matched %s pattern '%s'", category, action, pattern.pattern)
                matched.extend(t for t in tags if t not in matched)

        if not matched:
            self.logger.debug(
                "No %s tags mapped for category '%s' (%s), skipping",
                action,
                category,
                task.hash[:8],
            )
            return

        try:
            self.client.add_torrents_tag(hashes=task.hash, tags=matched)
            self.logger.info(
                "Tagged %s with %s (category='%s', action='%s')",
                task.hash[:8],
                matched,
                category,
                action,
            )
        except Exception as e:
            self.logger.error("Failed to tag %s for category '%s' (%s): %s", task.hash[:8], category, action, e)
