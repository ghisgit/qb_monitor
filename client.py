import time
import logging
import requests
import functools
from typing import Tuple, Type, Callable, Any
from qbittorrentapi import Client, APIError, TorrentFilesList, TorrentInfoList

logger = logging.getLogger(__name__)


def retry(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 1.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
):
    """
    重试装饰器

    Args:
        max_attempts: 最大重试次数（包括首次调用）
        delay: 初始延迟秒数
        backoff: 延迟倍增因子（delay *= backoff）
        exceptions: 需要重试的异常类型
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            current_delay = delay
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    if attempt == max_attempts:
                        logger.error(
                            f"💥 {func.__qualname__} failed after {max_attempts} attempts: {e}"
                        )
                        raise
                    else:
                        logger.warning(
                            f"⚠️ {func.__qualname__} failed (attempt {attempt}/{max_attempts}): {e}. "
                            f"Retrying in {current_delay:.1f}s..."
                        )
                        time.sleep(current_delay)
                        current_delay *= backoff
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
    def get_torrents(self) -> TorrentInfoList:
        return self.client.torrents_info()

    @retry(
        delay=1.0,
        exceptions=(APIError, requests.ReadTimeout),
    )
    def get_torrent_files(self, hash) -> TorrentFilesList:
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
        return self.client.torrents_file_priority(
            torrent_hash=hash, file_ids=file_ids, priority=0
        )

    @retry(
        delay=1.0,
        exceptions=(APIError, requests.ReadTimeout),
    )
    def move_to_bottom(self, hashes):
        self.client.torrents_bottom_priority(torrent_hashes=hashes)
