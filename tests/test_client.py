import time
from unittest.mock import MagicMock, patch

import pytest
from qbittorrentapi import APIError

from _breaker import CircuitBreakerConfig, CircuitBreakerOpenError, RetryConfig
from client import QBittorrentClient


def _make_mock(return_value="ok", side_effect=None):
    m = MagicMock(return_value=return_value, side_effect=side_effect)
    m.__qualname__ = "mock_method"
    return m


@pytest.fixture
def client():
    with (
        patch("client.Client") as mock_cls,
        patch("client.time.sleep"),
    ):
        mock_cls.return_value.auth_log_in = MagicMock()
        instance = QBittorrentClient(
            host="http://localhost:8080",
            username="admin",
            password="admin",
            retry_cfg=RetryConfig(max_attempts=3, delay=0.01, backoff=1.0, cap=30.0),
            breaker_cfg=CircuitBreakerConfig(failure_threshold=5, recovery_timeout=60.0),
        )
        yield instance


class TestRequest:
    def test_successful_call_returns_result(self, client):
        method = _make_mock(return_value="ok")
        assert client._request(method) == "ok"
        method.assert_called_once()

    def test_transient_failure_then_success(self, client):
        method = _make_mock(side_effect=[APIError("timeout"), APIError("timeout"), "recovered"])
        assert client._request(method) == "recovered"
        assert method.call_count == 3

    def test_persistent_failure_raises(self, client):
        method = _make_mock(side_effect=APIError("boom"))
        with pytest.raises(APIError, match="boom"):
            client._request(method)
        assert method.call_count == 3

    def test_non_retryable_exception_propagates_immediately(self, client):
        method = _make_mock(side_effect=ValueError("not retryable"))
        with pytest.raises(ValueError, match="not retryable"):
            client._request(method)
        method.assert_called_once()

    def test_circuit_breaker_open_fast_fails(self, client):
        for _ in range(5):
            client._breaker.on_failure()
        assert not client._breaker.allow_request()
        method = _make_mock()
        with pytest.raises(CircuitBreakerOpenError):
            client._request(method)
        method.assert_not_called()

    def test_circuit_breaker_recovers_after_timeout(self, client):
        for _ in range(5):
            client._breaker.on_failure()
        client._breaker._last_failure_time = time.monotonic() - 61.0
        method = _make_mock(return_value="recovered")
        assert client._request(method) == "recovered"

    def test_health_returns_true_when_reachable(self, client):
        client.client.app_version = MagicMock(return_value="v4.6.0")
        assert client.health()

    def test_health_returns_false_on_failure(self, client):
        client.client.app_version = MagicMock(side_effect=APIError("down"))
        assert not client.health()

    def test_get_max_active_downloads_returns_cached_on_failure(self, client):
        client.client.app_preferences = MagicMock(side_effect=APIError("down"))
        result = client.get_max_active_downloads()
        assert result == 3

    def test_get_max_active_downloads_fetches_from_preferences(self, client):
        client.client.app_preferences = MagicMock(return_value={"max_active_downloads": 8})
        result = client.get_max_active_downloads()
        assert result == 8

    def test_public_methods_delegate_to_request(self, client):
        client._request = _make_mock(return_value="ok")
        assert client.get_torrents() == "ok"
        assert client.get_torrent_files(torrent_hash="abc") == "ok"
        assert client.add_torrents_tag(hashes=["a"], tag="t") == "ok"
        assert client.remove_torrents_tag(hashes=["a"], tag="t") == "ok"
        assert client.set_file_no_download(torrent_hash="a", file_ids=[1]) == "ok"
        assert client.move_to_bottom(hashes=["a"]) == "ok"
