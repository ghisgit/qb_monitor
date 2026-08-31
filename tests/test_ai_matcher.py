import threading
from types import SimpleNamespace
from typing import Any, cast

import pytest

from core.models import TaskDeferredError
from handlers.organize.matcher import DeepSeekMatcher, MatcherConfig, MatchError, PlanFile

MOVIE_RESPONSE = (
    '```json\n{"kind": "movie", "title": "Movie Name", "year": 2023, "tmdb_id": 555,'
    ' "files": [{"file": "Movie.Name.2023.1080p.mkv"}]}\n```'
)

TV_RESPONSE = (
    '{"kind": "tv", "title": "Show", "year": 2021, "tmdb_id": 9,'
    ' "files": [{"file": "Show/Season 01/S01E01.mkv", "season": 1, "episodes": [1]}]}'
)

VALID_CONTEXT = {
    "hash": "a" * 40,
    "name": "Movie.Name.2023.1080p.mkv",
    "category": "movies",
    "files": [{"file": "Movie.Name.2023.1080p.mkv", "size": 1000}],
}


class FakeHarness:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def run(self, prompt, session_id=None):
        self.calls.append((prompt, session_id))
        item = self.results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make_matcher(results, ai_retries=0, **cfg_kwargs):
    cfg = MatcherConfig(tmdb_api_key="tmdb-secret", ai_retries=ai_retries, **cfg_kwargs)
    matcher = DeepSeekMatcher(cfg)
    matcher._harness = cast(Any, FakeHarness(results))
    return matcher


def harness_calls(matcher) -> list:
    """读取测试用 FakeHarness 的调用记录（绕过 _harness 的 DeepSeekHarness 类型）。"""
    return cast(FakeHarness, cast(Any, matcher._harness)).calls


def response(payload: str, finish_reason: str = "completed") -> SimpleNamespace:
    return SimpleNamespace(final_response=payload, finish_reason=finish_reason)


class TestResponseExtraction:
    def test_fenced_json(self):
        matcher = make_matcher([response(MOVIE_RESPONSE)])
        plan = matcher.match(VALID_CONTEXT)
        assert plan.kind == "movie"
        assert plan.title == "Movie Name"
        assert plan.year == 2023
        assert plan.tmdb_id == 555
        assert plan.files == [PlanFile(file="Movie.Name.2023.1080p.mkv")]

    def test_raw_json_with_prose(self):
        payload = "好的，匹配结果如下：\n" + TV_RESPONSE + "\n以上。"
        matcher = make_matcher([response(payload)])
        plan = matcher.match(
            {
                **VALID_CONTEXT,
                "name": "Show.S01E01",
                "files": [{"file": "Show/Season 01/S01E01.mkv", "size": 1}],
            }
        )
        assert plan.kind == "tv"
        assert plan.files[0].episodes == [1]

    def test_preference_order_is_plain_then_fence_then_raw(self):
        payload = '{"kind": "movie", "title": "One", "year": 2000, "tmdb_id": 1, "files": [{"file": "Movie.Name.2023.1080p.mkv"}]}'
        matcher = make_matcher([response(payload)])
        plan = matcher.match(VALID_CONTEXT)
        assert plan.title == "One"


class TestValidation:
    def test_invalid_kind_rejected(self):
        payload = '{"kind": "show", "title": "T", "year": 2020, "tmdb_id": 1, "files": []}'
        matcher = make_matcher([response(payload)])
        with pytest.raises(MatchError):
            matcher.match(VALID_CONTEXT)

    def test_missing_title_rejected(self):
        payload = '{"kind": "movie", "year": 2020, "tmdb_id": 1, "files": []}'
        matcher = make_matcher([response(payload)])
        with pytest.raises(MatchError):
            matcher.match(VALID_CONTEXT)

    def test_bad_year_rejected(self):
        payload = '{"kind": "movie", "title": "T", "year": 1800, "tmdb_id": 1, "files": []}'
        matcher = make_matcher([response(payload)])
        with pytest.raises(MatchError):
            matcher.match(VALID_CONTEXT)

    def test_bool_year_rejected(self):
        payload = '{"kind": "movie", "title": "T", "year": true, "tmdb_id": 1, "files": []}'
        matcher = make_matcher([response(payload)])
        with pytest.raises(MatchError):
            matcher.match(VALID_CONTEXT)

    def test_negative_tmdb_id_rejected(self):
        payload = '{"kind": "movie", "title": "T", "year": 2020, "tmdb_id": -1, "files": []}'
        matcher = make_matcher([response(payload)])
        with pytest.raises(MatchError):
            matcher.match(VALID_CONTEXT)

    def test_file_not_in_torrent_rejected(self):
        payload = '{"kind": "movie", "title": "T", "year": 2020, "tmdb_id": 1, "files": [{"file": "Other.file.mkv"}]}'
        matcher = make_matcher([response(payload)])
        with pytest.raises(MatchError):
            matcher.match(VALID_CONTEXT)

    def test_path_traversal_rejected(self):
        payload = '{"kind": "movie", "title": "T", "year": 2020, "tmdb_id": 1, "files": [{"file": "../../etc/passwd"}]}'
        matcher = make_matcher([response(payload)])
        with pytest.raises(MatchError):
            matcher.match(VALID_CONTEXT)

    def test_empty_episodes_rejected(self):
        payload = (
            '{"kind": "tv", "title": "S", "year": 2020, "tmdb_id": 1,'
            ' "files": [{"file": "x.mkv", "season": 1, "episodes": []}]}'
        )
        matcher = make_matcher([response(payload)])
        with pytest.raises(MatchError):
            matcher.match({**VALID_CONTEXT, "files": [{"file": "x.mkv", "size": 1}]})

    def test_duplicate_episode_mapping_rejected(self):
        payload = (
            '{"kind": "tv", "title": "S", "year": 2020, "tmdb_id": 1,'
            ' "files": [{"file": "a.mkv", "season": 1, "episodes": [1]},'
            ' {"file": "b.mkv", "season": 1, "episodes": [1]}]}'
        )
        matcher = make_matcher([response(payload)])
        with pytest.raises(MatchError):
            matcher.match({**VALID_CONTEXT, "files": [{"file": "a.mkv", "size": 1}, {"file": "b.mkv", "size": 1}]})

    def test_empty_plan_rejected(self):
        payload = '{"kind": "movie", "title": "T", "year": 2020, "tmdb_id": 1, "files": []}'
        matcher = make_matcher([response(payload)])
        with pytest.raises(MatchError):
            matcher.match(VALID_CONTEXT)


class TestRetries:
    def test_transport_error_retries_then_raises(self):
        matcher = make_matcher(
            [TimeoutError("boom"), TimeoutError("boom"), TimeoutError("boom")],
            ai_retries=2,
        )
        with pytest.raises(MatchError):
            matcher.match(VALID_CONTEXT)
        assert len(harness_calls(matcher)) == 3

    def test_success_after_retry(self):
        matcher = make_matcher(
            [TimeoutError("boom"), response(MOVIE_RESPONSE)],
            ai_retries=1,
        )
        plan = matcher.match(VALID_CONTEXT)
        assert plan.title == "Movie Name"
        assert len(harness_calls(matcher)) == 2

    def test_abnormal_finish_reason_retried(self):
        matcher = make_matcher(
            [
                response(
                    '{"kind": "movie", "title": "T", "year": 2020, "tmdb_id": 1, "files": []}', finish_reason="error"
                ),
                response(MOVIE_RESPONSE),
            ],
            ai_retries=1,
        )
        plan = matcher.match(VALID_CONTEXT)
        assert plan.title == "Movie Name"

    def test_task_deferred_bypasses_retry_loop(self):
        matcher = make_matcher([TaskDeferredError("busy"), TaskDeferredError("busy")], ai_retries=2)
        with pytest.raises(TaskDeferredError):
            matcher.match(VALID_CONTEXT)
        # 让位不消耗重试次数：仅一次调用
        assert len(harness_calls(matcher)) == 1

    def test_busy_slot_defers_instead_of_blocking(self):
        release = threading.Event()

        class SlowHarness:
            def __init__(self):
                self.entered = threading.Event()

            def run(self, prompt, session_id=None):
                self.entered.set()
                release.wait(5)
                return response(MOVIE_RESPONSE)

        matcher = make_matcher([], max_concurrent_sessions=1)
        harness = SlowHarness()
        matcher._harness = cast(Any, harness)
        errors: list = []

        def worker():
            try:
                matcher.match(VALID_CONTEXT)
            except Exception as e:
                errors.append(e)

        t = threading.Thread(target=worker)
        t.start()
        assert harness.entered.wait(5)  # worker 已占用唯一槽位
        with pytest.raises(TaskDeferredError):
            matcher.match(VALID_CONTEXT)
        release.set()
        t.join(timeout=5)
        assert errors == []

    def test_concurrent_sessions_run_in_parallel(self):
        barrier = threading.Barrier(2, timeout=5)

        class BarrierHarness:
            def run(self, prompt, session_id=None):
                # 仅当两个 AI 轮次真正并发时，两个线程才能同时到达 Barrier
                barrier.wait()
                return response(MOVIE_RESPONSE)

        matcher = make_matcher([], max_concurrent_sessions=2)
        matcher._harness = cast(Any, BarrierHarness())
        results: list = []

        def run_match():
            try:
                results.append(matcher.match(VALID_CONTEXT))
            except Exception as e:  # noqa: BLE001
                results.append(e)

        threads = [threading.Thread(target=run_match) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert len(results) == 2
        for r in results:
            assert r.title == "Movie Name"

    def test_non_json_output_raises_match_error(self):
        matcher = make_matcher(
            [response("这个种子看起来是一部 2023 年的电影。"), response("我也不清楚，无法输出。")],
            ai_retries=0,
        )
        with pytest.raises(MatchError):
            matcher.match(VALID_CONTEXT)
        assert len(harness_calls(matcher)) == 2

    def test_corrective_turn_recovers_json_on_same_session(self):
        matcher = make_matcher(
            [response("这是《牧神记》第 91 集。"), response(MOVIE_RESPONSE)],
            ai_retries=0,
        )
        plan = matcher.match(VALID_CONTEXT)
        assert plan.title == "Movie Name"
        calls = harness_calls(matcher)
        assert len(calls) == 2
        # 纠正追问复用同一 session 且是纠正提示
        assert calls[1][1] == calls[0][1]
        assert "不是合法的 JSON" in calls[1][0]


class TestPrompt:
    def test_prompt_contains_context_but_no_secret(self):
        matcher = make_matcher([response(MOVIE_RESPONSE)])
        matcher.match(VALID_CONTEXT)
        prompt, _session_id = harness_calls(matcher)[0]
        assert "Movie.Name.2023.1080p.mkv" in prompt
        assert "tmdb-secret" not in prompt
        assert "TMDB_API_KEY" in prompt
        assert "movies" in prompt

    def test_prompt_has_no_curl_and_uses_urllib(self):
        matcher = make_matcher([response(MOVIE_RESPONSE)])
        matcher.match(VALID_CONTEXT)
        prompt, _ = harness_calls(matcher)[0]
        # 明确告知无 curl，且不提供任何 curl 命令示例（"curl:" 这类用法）
        assert "没有 curl" in prompt
        assert "curl -" not in prompt
        assert "urllib" in prompt
        # key 兜底：env 缺失时从 config.yaml 读取
        assert "organize.tmdb_api_key" in prompt

    def test_prompt_without_proxy_has_no_hint(self):
        matcher = make_matcher([response(MOVIE_RESPONSE)])
        matcher.match(VALID_CONTEXT)
        prompt, _ = harness_calls(matcher)[0]
        assert "ProxyHandler" not in prompt
        assert "网络要点" not in prompt

    def test_prompt_with_proxy_contains_recipe(self):
        matcher = make_matcher([response(MOVIE_RESPONSE)], tmdb_proxy="http://192.168.0.88:7890")
        matcher.match(VALID_CONTEXT)
        prompt, _ = harness_calls(matcher)[0]
        assert "网络要点" in prompt
        assert "http://192.168.0.88:7890" in prompt
        assert 'ProxyHandler({"https": "http://192.168.0.88:7890"' in prompt
        assert "不要尝试直连" in prompt

    def test_session_ids_unique_and_prefixed(self):
        matcher = make_matcher([response(MOVIE_RESPONSE), response(MOVIE_RESPONSE)])
        matcher.match(VALID_CONTEXT)
        matcher.match(VALID_CONTEXT)
        ids = [call[1] for call in harness_calls(matcher)]
        assert ids[0] != ids[1]
        assert all(sid.startswith("organize-") for sid in ids)
        assert all(VALID_CONTEXT["hash"][:12] in sid for sid in ids)

    def test_language_config_in_prompt(self):
        matcher = make_matcher([response(MOVIE_RESPONSE)], language="en-US")
        matcher.match(VALID_CONTEXT)
        prompt, _ = harness_calls(matcher)[0]
        assert "language=en-US" in prompt


def test_matcher_config_requires_tmdb_key():
    with pytest.raises(ValueError):
        MatcherConfig(tmdb_api_key="")


def test_matcher_config_validates_max_concurrent_sessions():
    with pytest.raises(ValueError, match="max_concurrent_sessions"):
        MatcherConfig(tmdb_api_key="k", max_concurrent_sessions=0)
    with pytest.raises(ValueError, match="max_concurrent_sessions"):
        MatcherConfig(tmdb_api_key="k", max_concurrent_sessions=True)


def test_matcher_config_accepts_empty_tmdb_proxy():
    cfg = MatcherConfig(tmdb_api_key="k")
    assert cfg.tmdb_proxy == ""


def test_close_is_safe_before_start():
    matcher = DeepSeekMatcher(MatcherConfig(tmdb_api_key="k"))
    matcher.close()  # 未启动 runtime 时 close 应为空操作
