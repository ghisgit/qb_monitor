import re
from typing import Any
from unittest.mock import MagicMock

from qbittorrentapi import TorrentDictionary

from handlers.category_tag import CategoryTagHandler


def make_task(category: str | None = None, include_key: bool = True) -> TorrentDictionary:
    data: dict[str, Any] = {"hash": "a" * 40, "name": "test torrent", "tags": ""}
    if include_key:
        data["category"] = category
    return TorrentDictionary(client=MagicMock(), data=data)


def make_handler(mappings: dict) -> tuple[CategoryTagHandler, MagicMock]:
    client = MagicMock()
    return CategoryTagHandler(client, mappings), client


class TestMappingNormalization:
    def test_string_value_normalized_to_list(self):
        handler, _ = make_handler({"added": {"^movies": "电影"}})
        assert next(iter(handler.mappings["added"].values())) == ["电影"]

    def test_patterns_compiled_case_insensitive(self):
        handler, _ = make_handler({"added": {"^movies": "电影"}})
        pattern = next(iter(handler.mappings["added"]))
        assert pattern.pattern == "^movies"
        assert pattern.flags & re.IGNORECASE

    def test_only_configured_actions_stored(self):
        handler, _ = make_handler({"added": {"^m": "t"}})
        assert set(handler.mappings) == {"added"}


class TestActionRouting:
    def test_handle_added_applies_added_mapping_only(self):
        handler, client = make_handler(
            {
                "added": {"^movies": "电影"},
                "completed": {"^movies": "清洗后"},
            }
        )

        handler.handle_added(make_task("movies"))

        client.add_torrents_tag.assert_called_once_with(hashes="a" * 40, tags=["电影"])

    def test_handle_completed_applies_completed_mapping_only(self):
        handler, client = make_handler(
            {
                "added": {"^movies": "电影"},
                "completed": {"^movies": "清洗后"},
            }
        )

        handler.handle_completed(make_task("movies"))

        client.add_torrents_tag.assert_called_once_with(hashes="a" * 40, tags=["清洗后"])

    def test_unconfigured_action_is_noop(self):
        handler, client = make_handler({"added": {"^movies": "电影"}})

        handler.handle_completed(make_task("movies"))

        client.add_torrents_tag.assert_not_called()


class TestRegexMatching:
    def test_subcategory_cascade_via_regex(self):
        # 正则匹配带来子分类级联：^movies 命中 movies/4K
        handler, client = make_handler({"added": {"^movies": "电影"}})

        handler.handle_added(make_task("movies/4K"))

        client.add_torrents_tag.assert_called_once_with(hashes="a" * 40, tags=["电影"])

    def test_search_semantics_match_mid_string(self):
        handler, client = make_handler({"added": {"movies": "电影"}})

        handler.handle_added(make_task("xx-movies-yy"))

        client.add_torrents_tag.assert_called_once()

    def test_case_insensitive_match(self):
        handler, client = make_handler({"added": {"^movies": "电影"}})

        handler.handle_added(make_task("Movies"))

        client.add_torrents_tag.assert_called_once()

    def test_no_match_skips_api_call(self):
        handler, client = make_handler({"added": {"^movies": "电影"}})

        handler.handle_added(make_task("books"))

        client.add_torrents_tag.assert_not_called()

    def test_multiple_matching_patterns_merged_and_deduped(self):
        # 每个种子只有一个分类，但可命中多个键：标签合并去重
        handler, client = make_handler({"added": {"^movies": ["电影"], "4K": ["4K", "电影"]}})

        handler.handle_added(make_task("movies/4K"))

        client.add_torrents_tag.assert_called_once_with(hashes="a" * 40, tags=["电影", "4K"])


class TestEmptyCategory:
    def test_empty_category_skips_api_call(self):
        handler, client = make_handler({"added": {"^movies": "电影"}})

        handler.handle_added(make_task(""))

        client.add_torrents_tag.assert_not_called()

    def test_missing_category_key_skips_api_call(self):
        handler, client = make_handler({"added": {"^movies": "电影"}})

        handler.handle_added(make_task(None, include_key=False))

        client.add_torrents_tag.assert_not_called()

    def test_whitespace_category_stripped_before_match(self):
        handler, client = make_handler({"added": {"^movies": "电影"}})

        handler.handle_added(make_task(" movies "))

        client.add_torrents_tag.assert_called_once_with(hashes="a" * 40, tags=["电影"])


class TestApiFailure:
    def test_api_failure_swallowed_not_raised(self):
        handler, client = make_handler({"added": {"^movies": "电影"}})
        client.add_torrents_tag.side_effect = RuntimeError("api down")

        # API 失败 → error 日志，不抛出
        handler.handle_added(make_task("movies"))

        client.add_torrents_tag.assert_called_once()
