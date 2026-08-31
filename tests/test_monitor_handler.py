import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

from handlers.monitor import MonitorHandler


def make_torrent(hash_, name, progress=0.5, state="downloading"):
    return SimpleNamespace(hash=hash_, name=name, progress=progress, state=state)


def make_handler(stall_timeout=1.0, client=None):
    return MonitorHandler(client or MagicMock(), stall_timeout_seconds=stall_timeout)


def batch(torrents, downloading_count=2, demotion_threshold=4, now=100):
    return {
        "torrents": torrents,
        "downloading_count": downloading_count,
        "demotion_threshold": demotion_threshold,
        "now": now,
    }


class TestMonitorLogging:
    def test_no_change_cycle_is_silent(self, caplog):
        handler = make_handler(stall_timeout=1000)
        t = make_torrent("a" * 40, "T1")
        handler.handle(batch([t], now=100))
        caplog.set_level(logging.DEBUG)
        caplog.clear()

        handler.handle(batch([t], now=101))  # 无进度、未停滞、低于阈值 → 整轮静默
        assert caplog.records == []

    def test_stall_event_fires_once_then_silent(self, caplog):
        handler = make_handler(stall_timeout=1.0)
        t = make_torrent("b" * 40, "S1")
        handler.handle(batch([t], now=100))
        caplog.set_level(logging.DEBUG)

        handler.handle(batch([t], now=150))  # 100 → 150 空闲超过 1s → 停滞告警
        warns = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warns) == 1 and "Stalled" in warns[0].getMessage()

        caplog.clear()
        handler.handle(batch([t], now=151))  # 仍停滞、无新变化 → 不再重复告警
        assert caplog.records == []

    def test_recovery_logged_after_stall(self, caplog):
        handler = make_handler(stall_timeout=1.0)
        t = make_torrent("c" * 40, "R1")
        handler.handle(batch([t], now=100))
        handler.handle(batch([t], now=150))  # 停滞
        caplog.set_level(logging.DEBUG)
        caplog.clear()

        t.progress = 0.6  # 恢复进度
        handler.handle(batch([t], now=200))
        infos = [r for r in caplog.records if r.levelno == logging.INFO]
        assert len(infos) == 1 and "Recovered" in infos[0].getMessage()

    def test_demotion_only_above_threshold(self):
        client = MagicMock()
        handler = make_handler(stall_timeout=1.0, client=client)
        t = make_torrent("d" * 40, "D1")
        handler.handle(batch([t], now=100))

        # 高于阈值：停滞种子被降级
        handler.handle(batch([t], now=150, downloading_count=10, demotion_threshold=4))
        client.move_to_bottom.assert_called_once_with(hashes=["d" * 40])

    def test_no_demotion_below_threshold(self):
        client = MagicMock()
        handler = make_handler(stall_timeout=1.0, client=client)
        t = make_torrent("e" * 40, "E1")
        handler.handle(batch([t], now=100))

        # 低于阈值：即使停滞也不降级
        handler.handle(batch([t], now=150, downloading_count=2, demotion_threshold=4))
        client.move_to_bottom.assert_not_called()

    def test_removed_when_no_longer_active(self, caplog):
        handler = make_handler(stall_timeout=1000)
        t = make_torrent("f" * 40, "F1")
        handler.handle(batch([t], now=100))
        caplog.set_level(logging.DEBUG)
        caplog.clear()

        handler.handle(batch([], now=200))
        debugs = [r for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("Stop tracking" in r.getMessage() for r in debugs)

    def test_summary_only_when_changed(self, caplog):
        handler = make_handler(stall_timeout=1000)
        t = make_torrent("g" * 40, "G1")
        caplog.set_level(logging.DEBUG)
        handler.handle(batch([t], now=100))  # 新跟踪 → 有变化 → 出摘要
        assert any("monitor:" in r.getMessage() for r in caplog.records)

        caplog.clear()
        handler.handle(batch([t], now=101))  # 无变化 → 无摘要
        assert caplog.records == []
