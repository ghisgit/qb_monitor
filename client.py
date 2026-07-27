import functools
import time
from collections.abc import Callable
from typing import Any

import requests
from qbittorrentapi import APIError, Client, TorrentFilesList, TorrentInfoList

from logger import get_logger

logger = get_logger(__name__)


def retry(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 1.0,
    exceptions: tuple[type[Exception], ...] = (Exception,),
):
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            current_delay = min(delay, 30)
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    if attempt == max_attempts:
                        logger.error(
                            "%s failed after %d attempts: %s",
                            func.__qualname__,
                            max_attempts,
                            e,
                        )
                        raise
                    else:
                        logger.warning(
                            "%s failed (attempt %d/%d): %s. Retrying in %.1fs...",
                            func.__qualname__,
                            attempt,
                            max_attempts,
                            e,
                            current_delay,
                        )
                        time.sleep(current_delay)
                        current_delay = min(current_delay * backoff, 30)
            return None

        return wrapper

    return decorator


class QBittorrentClient:
    def __init__(self, host: str, username: str, password: str):
        self.client = Client(
            host=host,
            username=username,
            password=password,
            REQUESTS_ARGS={"timeout": (5, 30)},
        )
        self.client.auth_log_in(username=username, password=password)

    @retry(
        delay=2.0,
        backoff=1.5,
        exceptions=(APIError, requests.ReadTimeout),
    )
    def get_torrents(self, tag: str | None = None, status_filter: str | None = None) -> TorrentInfoList:
        return self.client.torrents_info(tag=tag, status_filter=status_filter)  # type: ignore[arg-type]

    @retry(
        delay=1.0,
        exceptions=(APIError, requests.ReadTimeout),
    )
    def get_torrent_files(self, hash: str | None = None) -> TorrentFilesList:
        return self.client.torrents_files(torrent_hash=hash)

    @retry(
        delay=1.0,
        exceptions=(APIError, requests.ReadTimeout),
    )
    def add_torrents_tag(self, hashes, tag):
        return self.client.torrents_add_tags(torrent_hashes=hashes, tags=tag)

    @retry(
        delay=1.0,
        exceptions=(APIError, requests.ReadTimeout),
    )
    def remove_torrents_tag(self, hashes, tag):
        return self.client.torrents_remove_tags(torrent_hashes=hashes, tags=tag)

    @retry(
        delay=1.0,
        exceptions=(APIError, requests.ReadTimeout),
    )
    def set_file_no_download(self, hash, file_ids):
        return self.client.torrents_file_priority(torrent_hash=hash, file_ids=file_ids, priority=0)

    @retry(
        delay=1.0,
        exceptions=(APIError, requests.ReadTimeout),
    )
    def move_to_bottom(self, hashes):
        return self.client.torrents_bottom_priority(torrent_hashes=hashes)
