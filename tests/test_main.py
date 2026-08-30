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


def organize_config() -> dict:
    data = base_config()
    data["organize"] = {
        "enabled": True,
        "tags": ["organize"],
        "library": {"movies_dir": "/media/movies", "tv_dir": "/media/tv", "fallback_dir": "/media/unmatched"},
        "tmdb_api_key": "tmdb-key",
        "on_exists": "skip",
        "on_match_failure": "fallback",
        "ai_retries": 1,
        "min_file_size_mb": 0,
        "include_episode_title": False,
        "include_tmdb_id": True,
        "video_extensions": [".mp4", ".mkv"],
        "dsh": {
            "model": "deepseek-v4-flash",
            "api_key": "sk-test",
            "base_url": "",
            "language": "zh-CN",
            "request_timeout_seconds": 300,
            "session_root": "sessions",
        },
    }
    return data


class TestOrganizeValidation:
    def test_absent_section_passes(self):
        validate_config(base_config())

    def test_empty_section_passes(self):
        data = base_config()
        data["organize"] = {}
        validate_config(data)

    def test_disabled_section_passes_without_keys(self):
        data = base_config()
        data["organize"] = {"enabled": False}
        validate_config(data)

    def test_valid_section_passes(self):
        validate_config(organize_config())

    def test_api_key_from_environment_passes(self, monkeypatch):
        data = organize_config()
        data["organize"]["dsh"]["api_key"] = ""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env")
        validate_config(data)

    def test_api_key_missing_rejected(self, monkeypatch):
        data = organize_config()
        data["organize"]["dsh"]["api_key"] = ""
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
            validate_config(data)

    def test_missing_tmdb_key_rejected(self):
        data = organize_config()
        data["organize"]["tmdb_api_key"] = ""
        with pytest.raises(ValueError, match="tmdb_api_key"):
            validate_config(data)

    def test_missing_library_dir_rejected(self):
        data = organize_config()
        del data["organize"]["library"]["fallback_dir"]
        with pytest.raises(ValueError, match="fallback_dir"):
            validate_config(data)

    def test_empty_tags_rejected(self):
        data = organize_config()
        data["organize"]["tags"] = []
        with pytest.raises(ValueError, match="tags"):
            validate_config(data)

    def test_non_list_tags_rejected(self):
        data = organize_config()
        data["organize"]["tags"] = "organize"
        with pytest.raises(ValueError, match="tags"):
            validate_config(data)

    def test_invalid_on_exists_rejected(self):
        data = organize_config()
        data["organize"]["on_exists"] = "replace"
        with pytest.raises(ValueError, match="on_exists"):
            validate_config(data)

    def test_invalid_on_match_failure_rejected(self):
        data = organize_config()
        data["organize"]["on_match_failure"] = "ignore"
        with pytest.raises(ValueError, match="on_match_failure"):
            validate_config(data)

    def test_negative_min_size_rejected(self):
        data = organize_config()
        data["organize"]["min_file_size_mb"] = -1
        with pytest.raises(ValueError, match="min_file_size_mb"):
            validate_config(data)

    def test_invalid_video_extensions_rejected(self):
        data = organize_config()
        data["organize"]["video_extensions"] = []
        with pytest.raises(ValueError, match="video_extensions"):
            validate_config(data)

    def test_negative_ai_retries_rejected(self):
        data = organize_config()
        data["organize"]["ai_retries"] = -1
        with pytest.raises(ValueError, match="ai_retries"):
            validate_config(data)

    def test_non_bool_flag_rejected(self):
        data = organize_config()
        data["organize"]["include_tmdb_id"] = "yes"
        with pytest.raises(ValueError, match="include_tmdb_id"):
            validate_config(data)

    def test_non_bool_enabled_rejected(self):
        data = organize_config()
        data["organize"]["enabled"] = 1
        with pytest.raises(ValueError, match="enabled"):
            validate_config(data)

    def test_invalid_timeout_rejected(self):
        data = organize_config()
        data["organize"]["dsh"]["request_timeout_seconds"] = 0
        with pytest.raises(ValueError, match="request_timeout_seconds"):
            validate_config(data)

    def test_empty_model_rejected(self):
        data = organize_config()
        data["organize"]["dsh"]["model"] = "  "
        with pytest.raises(ValueError, match="model"):
            validate_config(data)

    def test_non_string_base_url_rejected(self):
        data = organize_config()
        data["organize"]["dsh"]["base_url"] = 123
        with pytest.raises(ValueError, match="base_url"):
            validate_config(data)
