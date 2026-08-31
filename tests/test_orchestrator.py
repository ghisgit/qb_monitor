import queue
from unittest.mock import MagicMock

import pytest
from qbittorrentapi import TorrentDictionary

from core.orchestrator import TorrentOrchestrator


def make_torrent(**kwargs) -> TorrentDictionary:
    data = {
        "hash": "abcdef1234567890",
        "name": "test torrent",
        "tags": "",
        "state": "stoppedUP",
        "progress": 1.0,
        "has_metadata": True,
        "category": "",
    }
    data.update(kwargs)
    return TorrentDictionary(client=MagicMock(), data=data)


def make_orchestrator(torrents: list[TorrentDictionary]):
    client = MagicMock()
    client.get_torrents.return_value = torrents
    task_queue = queue.Queue()
    orch = TorrentOrchestrator(client=client, task_queue=task_queue, poll_interval_seconds=1)
    return orch, client, task_queue


class TestRegister:
    def test_register_handler_defaults_to_no_post_chain(self):
        orch = TorrentOrchestrator(MagicMock(), queue.Queue())
        orch.register_handler("added", lambda t: None)
        assert "added" in orch._dispatcher
        assert "added" not in orch._post_tags

    def test_register_handler_with_post_chain(self):
        orch = TorrentOrchestrator(MagicMock(), queue.Queue())
        orch.register_handler("added", lambda t: None, enable_post_chain=True)
        assert "added" in orch._post_tags

    def test_register_batch_handler(self):
        orch = TorrentOrchestrator(MagicMock(), queue.Queue())
        orch.register_batch_handler("monitoring", lambda t: None)
        assert "monitoring" in orch._batch_dispatcher

    def test_register_post_handler_appends_in_order(self):
        orch = TorrentOrchestrator(MagicMock(), queue.Queue())
        orch.register_post_handler(lambda t: None)
        orch.register_post_handler(lambda t: None)
        assert len(orch._post_handlers) == 2


class TestProcessTorrentsUnifiedEnqueue:
    def test_added_and_completed_unified_enqueue_without_removing_trigger_tags(self):
        added = make_torrent(tags="added")
        completed = make_torrent(hash="1234567890abcdef", tags="completed")
        orch, client, task_queue = make_orchestrator([added, completed])
        orch.register_handler("added", lambda t: None)
        orch.register_handler("completed", lambda t: None)

        orch._process_torrents()

        assert task_queue.qsize() == 2
        client.add_torrents_tag.assert_called_once_with(hashes=[added.hash, completed.hash], tags="processing")
        # 触发标签不再在轮询阶段移除，由 dispatch 成功后统一移除
        client.remove_torrents_tag.assert_not_called()

    def test_torrent_without_trigger_tag_not_queued(self):
        orch, client, task_queue = make_orchestrator([make_torrent(tags="unrelated")])
        orch._process_torrents()
        assert task_queue.qsize() == 0
        client.add_torrents_tag.assert_not_called()

    def test_torrent_without_metadata_skipped(self):
        orch, _, task_queue = make_orchestrator([make_torrent(tags="added", has_metadata=False)])
        orch._process_torrents()
        assert task_queue.qsize() == 0

    def test_processing_tag_skipped(self):
        orch, client, task_queue = make_orchestrator([make_torrent(tags="added,processing")])
        orch._process_torrents()
        assert task_queue.qsize() == 0
        client.add_torrents_tag.assert_not_called()

    def test_processing_tag_with_space_separator_blocks_requeue(self):
        # 回归：qBittorrent 多标签返回 ", "（逗号+空格），旧实现 split(",") 会产生
        # 带前导空格的 ' processing'，'processing' in tag_set 恒为 False →
        # 处理中的种子每个轮询周期被重复入队（长耗时处理器如 organize 必现）
        t = make_torrent(tags="added, processing")
        orch, client, task_queue = make_orchestrator([t])
        orch.register_handler("added", lambda x: None)

        orch._process_torrents()

        assert task_queue.qsize() == 0
        client.add_torrents_tag.assert_not_called()

    def test_torrent_with_two_trigger_tags_enqueued_once(self):
        t = make_torrent(tags="added,completed")
        orch, client, task_queue = make_orchestrator([t])
        orch.register_handler("added", lambda x: None)
        orch.register_handler("completed", lambda x: None)

        orch._process_torrents()

        assert task_queue.qsize() == 1
        client.add_torrents_tag.assert_called_once_with(hashes=[t.hash], tags="processing")

    def test_monitoring_batch_still_dispatched(self):
        t = make_torrent(state="downloading", progress=0.5, tags="")
        orch, client, task_queue = make_orchestrator([t])
        client.get_max_active_downloads.return_value = 3

        orch._process_torrents()

        assert task_queue.qsize() == 1
        batch = task_queue.get()
        assert batch["type"] == "monitoring"
        assert batch["torrents"] == [t]
        assert batch["demotion_threshold"] == 3


class TestDispatch:
    def test_dispatch_matched_handler_and_removes_trigger_tag(self):
        orch, client, _ = make_orchestrator([])
        handler = MagicMock()
        orch.register_handler("added", handler)
        task = make_torrent(tags="added")

        orch.dispatch(task)

        handler.assert_called_once_with(task)
        client.remove_torrents_tag.assert_called_once_with(hashes=task.hash, tag="added")

    def test_handler_runs_before_trigger_tag_removal(self):
        orch, client, _ = make_orchestrator([])
        events = []
        orch.register_handler("added", lambda t: events.append("handler"))
        client.remove_torrents_tag.side_effect = lambda **kw: events.append("remove")
        task = make_torrent(tags="added")

        orch.dispatch(task)

        assert events == ["handler", "remove"]

    def test_dispatch_no_match_warns_and_does_nothing(self):
        orch, client, _ = make_orchestrator([])
        handler = MagicMock()
        orch.register_handler("added", handler)

        orch.dispatch(make_torrent(tags="unrelated"))

        handler.assert_not_called()
        client.remove_torrents_tag.assert_not_called()

    def test_substring_of_trigger_tag_does_not_match(self):
        # 回归：旧实现 `tag in task.tags` 为子串判断，"notadded" 会误命中 "added"
        orch, client, _ = make_orchestrator([])
        handler = MagicMock()
        orch.register_handler("added", handler)

        orch.dispatch(make_torrent(tags="notadded"))

        handler.assert_not_called()
        client.remove_torrents_tag.assert_not_called()

    def test_handler_exception_propagates_without_removal_or_post_chain(self):
        orch, client, _ = make_orchestrator([])
        post = MagicMock()
        orch.register_handler("added", MagicMock(side_effect=RuntimeError("boom")), enable_post_chain=True)
        orch.register_post_handler(post)
        task = make_torrent(tags="added")

        with pytest.raises(RuntimeError, match="boom"):
            orch.dispatch(task)

        # 失败 → 不移除触发标签、不跑 post 链，下轮重入队重试
        client.remove_torrents_tag.assert_not_called()
        post.assert_not_called()

    def test_two_trigger_tags_dispatch_first_registered_only(self):
        orch, client, _ = make_orchestrator([])
        added_handler = MagicMock()
        completed_handler = MagicMock()
        orch.register_handler("added", added_handler)
        orch.register_handler("completed", completed_handler)
        task = make_torrent(tags="completed,added")

        orch.dispatch(task)

        added_handler.assert_called_once()
        completed_handler.assert_not_called()
        client.remove_torrents_tag.assert_called_once_with(hashes=task.hash, tag="added")

    def test_spaced_tags_dispatch_matches_exactly(self):
        # 回归：qBittorrent 的 ", "（逗号+空格）分隔，逐项去空白后精确匹配
        orch, client, _ = make_orchestrator([])
        handler = MagicMock()
        orch.register_handler("added", handler)

        orch.dispatch(make_torrent(tags=" completed , added "))

        handler.assert_called_once()
        client.remove_torrents_tag.assert_called_once_with(hashes="abcdef1234567890", tag="added")

    def test_unknown_task_type_ignored(self):
        orch, _, _ = make_orchestrator([])
        orch.dispatch("not a task")


class TestBatchDispatch:
    def test_batch_task_routed_by_type(self):
        orch, _, _ = make_orchestrator([])
        batch_handler = MagicMock()
        orch.register_batch_handler("monitoring", batch_handler)
        batch = {"type": "monitoring", "torrents": []}

        orch.dispatch(batch)

        batch_handler.assert_called_once_with(batch)

    def test_unknown_batch_type_ignored(self):
        orch, _, _ = make_orchestrator([])
        batch_handler = MagicMock()
        orch.register_batch_handler("monitoring", batch_handler)

        orch.dispatch({"type": "unknown"})

        batch_handler.assert_not_called()


class TestPostChain:
    def test_post_chain_runs_after_success(self):
        orch, _, _ = make_orchestrator([])
        post = MagicMock()
        orch.register_handler("added", lambda t: None, enable_post_chain=True)
        orch.register_post_handler(post)
        task = make_torrent(tags="added")

        orch.dispatch(task)

        post.assert_called_once_with(task)

    def test_post_chain_skipped_without_opt_in(self):
        orch, _, _ = make_orchestrator([])
        post = MagicMock()
        orch.register_handler("completed", lambda t: None)
        orch.register_post_handler(post)

        orch.dispatch(make_torrent(tags="completed"))

        post.assert_not_called()

    def test_post_handler_scoped_to_tag_only_runs_for_that_tag(self):
        # added/completed 的 post 处理器互不越界（CategoryTagHandler 按动作区分依赖此语义）
        orch, _, _ = make_orchestrator([])
        added_post = MagicMock()
        completed_post = MagicMock()
        orch.register_handler("added", lambda t: None, enable_post_chain=True)
        orch.register_handler("completed", lambda t: None, enable_post_chain=True)
        orch.register_post_handler(added_post, tags="added")
        orch.register_post_handler(completed_post, tags="completed")

        orch.dispatch(make_torrent(tags="added"))

        added_post.assert_called_once()
        completed_post.assert_not_called()

    def test_post_handler_scope_accepts_multiple_tags(self):
        orch, _, _ = make_orchestrator([])
        post = MagicMock()
        orch.register_handler("added", lambda t: None, enable_post_chain=True)
        orch.register_handler("completed", lambda t: None, enable_post_chain=True)
        orch.register_post_handler(post, tags=["added", "completed"])

        orch.dispatch(make_torrent(tags="completed"))
        orch.dispatch(make_torrent(hash="b" * 40, tags="added"))

        assert post.call_count == 2

    def test_post_handler_single_failure_does_not_stop_next(self):
        orch, _, _ = make_orchestrator([])
        failing = MagicMock(side_effect=RuntimeError("post boom"))
        following = MagicMock()
        orch.register_handler("added", lambda t: None, enable_post_chain=True)
        orch.register_post_handler(failing)
        orch.register_post_handler(following)

        orch.dispatch(make_torrent(tags="added"))

        failing.assert_called_once()
        following.assert_called_once()
