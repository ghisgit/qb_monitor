from typing import Any
from unittest.mock import MagicMock

from qbittorrentapi import TorrentDictionary

from handlers.category_tag_handler import CategoryTagHandler


def make_task(category: str | None = None, include_key: bool = True) -> TorrentDictionary:
    data: dict[str, Any] = {"hash": "a" * 40, "name": "test torrent", "tags": ""}
    if include_key:
        data["category"] = category
    return TorrentDictionary(client=MagicMock(), data=data)


def make_handler(mapping: dict) -> tuple[CategoryTagHandler, MagicMock]:
    client = MagicMock()
    return CategoryTagHandler(client, mapping), client


class TestMappingNormalization:
    def test_string_value_normalized_to_list(self):
        handler, _ = make_handler({"movies": "电影"})
        assert handler.mapping == {"movies": ["电影"]}

    def test_list_value_kept(self):
        handler, _ = make_handler({"anime": ["动漫", "追番"]})
        assert handler.mapping == {"anime": ["动漫", "追番"]}


class TestHandle:
    def test_match_single_tag(self):
        handler, client = make_handler({"movies": "电影"})

        handler.handle(make_task("movies"))

        client.add_torrents_tag.assert_called_once_with(hashes="a" * 40, tags=["电影"])

    def test_match_multiple_tags(self):
        handler, client = make_handler({"anime": ["动漫", "追番"]})

        handler.handle(make_task("anime"))

        client.add_torrents_tag.assert_called_once_with(hashes="a" * 40, tags=["动漫", "追番"])

    def test_no_match_skips_api_call(self):
        handler, client = make_handler({"movies": "电影"})

        handler.handle(make_task("books"))

        client.add_torrents_tag.assert_not_called()

    def test_empty_category_skips_api_call(self):
        handler, client = make_handler({"movies": "电影"})

        handler.handle(make_task(""))

        client.add_torrents_tag.assert_not_called()

    def test_missing_category_key_skips_api_call(self):
        handler, client = make_handler({"movies": "电影"})

        handler.handle(make_task(None, include_key=False))

        client.add_torrents_tag.assert_not_called()

    def test_whitespace_category_stripped_before_match(self):
        handler, client = make_handler({"movies": "电影"})

        handler.handle(make_task(" movies "))

        client.add_torrents_tag.assert_called_once_with(hashes="a" * 40, tags=["电影"])

    def test_subcategory_not_cascaded(self):
        # qB 子分类（如 movies/4K）仅精确匹配，不级联
        handler, client = make_handler({"movies": "电影"})

        handler.handle(make_task("movies/4K"))

        client.add_torrents_tag.assert_not_called()

    def test_api_failure_swallowed_not_raised(self):
        handler, client = make_handler({"movies": "电影"})
        client.add_torrents_tag.side_effect = RuntimeError("api down")

        # API 失败 → error 日志，不抛出
        handler.handle(make_task("movies"))

        client.add_torrents_tag.assert_called_once()
