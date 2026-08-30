import copy

import pytest

from main import validate_config

BASE_CONFIG = {
    "qbittorrent": {"host": "http://127.0.0.1:8080", "username": "admin", "password": "admin"},
    "processor": {"poll_interval_seconds": 30, "stall_timeout_hours": 1, "max_worker_threads": 3},
    "rules": {"added": [], "completed": []},
    "logging": {"logfile": "logs/qb_auto.log"},
}


def base_config() -> dict:
    return copy.deepcopy(BASE_CONFIG)


class TestCategoryTagsValidation:
    def test_absent_section_passes(self):
        validate_config(base_config())

    def test_empty_section_passes(self):
        data = base_config()
        data["category_tags"] = {}
        validate_config(data)

    def test_valid_added_and_completed_passes(self):
        data = base_config()
        data["category_tags"] = {
            "added": {"^movies": "电影", "^anime": ["动漫", "追番"]},
            "completed": {"^tv": "剧集"},
        }
        validate_config(data)

    def test_unknown_action_rejected(self):
        data = base_config()
        data["category_tags"] = {"paused": {"^m": "x"}}
        with pytest.raises(ValueError, match="action must be 'added' or 'completed'"):
            validate_config(data)

    def test_non_dict_action_mapping_rejected(self):
        data = base_config()
        data["category_tags"] = {"added": "^movies"}
        with pytest.raises(ValueError, match="non-empty mapping"):
            validate_config(data)

    def test_empty_action_mapping_rejected(self):
        data = base_config()
        data["category_tags"] = {"added": {}}
        with pytest.raises(ValueError, match="non-empty mapping"):
            validate_config(data)

    def test_invalid_regex_rejected(self):
        data = base_config()
        data["category_tags"] = {"added": {"[(": "电影"}}
        with pytest.raises(ValueError, match="valid regex"):
            validate_config(data)

    def test_blank_pattern_rejected(self):
        data = base_config()
        data["category_tags"] = {"added": {"  ": "电影"}}
        with pytest.raises(ValueError, match="patterns must be non-empty"):
            validate_config(data)

    def test_empty_string_tag_rejected(self):
        data = base_config()
        data["category_tags"] = {"added": {"^m": ""}}
        with pytest.raises(ValueError, match="non-empty string"):
            validate_config(data)

    def test_empty_tag_list_rejected(self):
        data = base_config()
        data["category_tags"] = {"added": {"^m": []}}
        with pytest.raises(ValueError, match="non-empty string"):
            validate_config(data)

    def test_non_string_tag_rejected(self):
        data = base_config()
        data["category_tags"] = {"added": {"^m": [1]}}
        with pytest.raises(ValueError, match="non-empty string"):
            validate_config(data)
