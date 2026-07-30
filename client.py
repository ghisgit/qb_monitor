import time
from collections.abc import Callable
from typing import Any

import requests
from qbittorrentapi import APIError, Client, TorrentFilesList, TorrentInfoList

from _breaker import CircuitBreaker, CircuitBreakerConfig, CircuitBreakerOpenError, RetryConfig
from logger import get_logger

logger = get_logger(__name__)


class QBittorrentClient:
    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        retry_cfg: RetryConfig | None = None,
        breaker_cfg: CircuitBreakerConfig | None = None,
        connect_timeout: float = 5.0,
        read_timeout: float = 30.0,
    ):
        self._retry_cfg = retry_cfg or RetryConfig()
        self._breaker = CircuitBreaker(breaker_cfg or CircuitBreakerConfig())
        self._retryable_exceptions = (APIError, requests.ReadTimeout)
        self._max_active_downloads: int = 3
        self._connect(host, username, password, connect_timeout, read_timeout)

    def _connect(self, host, username, password, connect_timeout, read_timeout):
        last_exc: Exception | None = None
        for attempt in range(1, 4):
            try:
                self.client = Client(
                    host=host,
                    username=username,
                    password=password,
                    REQUESTS_ARGS={"timeout": (connect_timeout, read_timeout)},
                )
                self.client.auth_log_in(username=username, password=password)
                logger.info("Connected to qBittorrent at %s", host)
                return
            except (APIError, requests.RequestException) as e:
                last_exc = e
                if attempt < 3:
                    time.sleep(2**attempt)
        raise ConnectionError(f"Failed to connect after 3 attempts: {last_exc}")

    def _request(self, method: Callable[..., Any], **kwargs: Any) -> Any:
        if not self._breaker.allow_request():
            logger.warning("Circuit breaker OPEN, fast-failing %s", method.__qualname__)
            raise CircuitBreakerOpenError(f"Circuit breaker is OPEN for {method.__qualname__}")

        cfg = self._retry_cfg
        current_delay = min(cfg.delay, cfg.cap)

        for attempt in range(1, cfg.max_attempts + 1):
            try:
                result = method(**kwargs)
                self._breaker.on_success()
                return result
            except self._retryable_exceptions as e:
                logger.warning(
                    "%s failed (attempt %d/%d): %s. Retrying in %.1fs...",
                    method.__qualname__,
                    attempt,
                    cfg.max_attempts,
                    e,
                    current_delay,
                )
                if attempt == cfg.max_attempts:
                    self._breaker.on_failure()
                    raise
                time.sleep(current_delay)
                current_delay = min(current_delay * cfg.backoff, cfg.cap)

    def get_max_active_downloads(self) -> int:
        try:
            prefs = self._request(self.client.app_preferences)
            value = prefs.get("max_active_downloads", 3)
            self._max_active_downloads = value
        except Exception as e:
            logger.warning(
                "Failed to fetch max_active_downloads, using cached value %d: %s", self._max_active_downloads, e
            )
        return self._max_active_downloads

    def health(self) -> bool:
        try:
            self.client.app_version()
            return True
        except Exception as e:
            logger.warning("Health check failed: %s", e)
            return False

    def setup_autorun(self):
        host = self.client.host.rstrip("/")

        cmd_added = f"bash -c 'curl -s -f -d \"hashes=%K&tags=added\" {host}/api/v2/torrents/addTags'"
        cmd_completed = f"bash -c 'curl -s -f -d \"hashes=%K&tags=completed\" {host}/api/v2/torrents/addTags'"

        prefs = {
            "autorun_enabled": True,
            "autorun_on_torrent_added_enabled": True,
            "autorun_on_torrent_added_program": cmd_added,
            "autorun_program": cmd_completed,
        }

        try:
            current = self._request(self.client.app_preferences)
            if (
                current.get("autorun_enabled") is True
                and current.get("autorun_on_torrent_added_enabled") is True
                and current.get("autorun_on_torrent_added_program") == cmd_added
                and current.get("autorun_program") == cmd_completed
            ):
                logger.debug("Autorun already configured, skipping")
                return
        except Exception:
            pass

        self._request(self.client.app_set_preferences, prefs=prefs)
        logger.info("Autorun configured for added/completed tags")

    def get_torrents(self, tag: str | None = None, status_filter: str | None = None) -> TorrentInfoList:
        return self._request(self.client.torrents_info, tag=tag, status_filter=status_filter)

    def get_torrent_files(self, torrent_hash: str | None = None) -> TorrentFilesList:
        return self._request(self.client.torrents_files, torrent_hash=torrent_hash)

    def add_torrents_tag(self, hashes: str | list[str], tag: str):
        return self._request(self.client.torrents_add_tags, torrent_hashes=hashes, tags=tag)

    def remove_torrents_tag(self, hashes: str | list[str], tag: str):
        return self._request(self.client.torrents_remove_tags, torrent_hashes=hashes, tags=tag)

    def set_file_no_download(self, torrent_hash: str, file_ids: list[int]):
        return self._request(
            self.client.torrents_file_priority,
            torrent_hash=torrent_hash,
            file_ids=file_ids,
            priority=0,
        )

    def move_to_bottom(self, hashes: str | list[str]):
        return self._request(self.client.torrents_bottom_priority, torrent_hashes=hashes)
