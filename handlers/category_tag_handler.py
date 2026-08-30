from qbittorrentapi import TorrentDictionary

from client import QBittorrentClient
from logger import get_logger


class CategoryTagHandler:
    """动作完成后按 qBittorrent 分类（Category）补打标签。

    分类检测使用 in（精确匹配）：种子分类与映射键名完全一致才命中，
    不做子串匹配、不级联子分类（如 movies/4K 不会命中 movies）。
    """

    def __init__(self, client: QBittorrentClient, mapping: dict[str, str | list[str]]):
        self.client = client
        self.logger = get_logger(self.__class__.__name__)
        # 归一化为 list[str]，兼容配置中的字符串或列表
        self.mapping: dict[str, list[str]] = {
            category.strip(): [tags] if isinstance(tags, str) else list(tags) for category, tags in mapping.items()
        }

    def handle(self, task: TorrentDictionary) -> None:
        raw_category = task.get("category")  # get() 兼容缺失字段，避免 AttributeError
        category = raw_category.strip() if isinstance(raw_category, str) else ""
        tags = self.mapping.get(category)  # in 精确检测
        if not tags:
            self.logger.debug(
                "No tags mapped for category '%s' (%s), skipping",
                category or "<empty>",
                task.hash[:8],
            )
            return

        try:
            self.client.add_torrents_tag(hashes=task.hash, tags=tags)
            self.logger.info("Tagged %s with %s (category='%s')", task.hash[:8], tags, category)
        except Exception as e:
            self.logger.error("Failed to tag %s for category '%s': %s", task.hash[:8], category, e)
