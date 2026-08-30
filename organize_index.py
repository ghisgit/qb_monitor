"""本地整理索引：记录已成功整理种子的匹配计划与目标路径。

用途：同一个种子再次打 organize 标签时，若文件清单（指纹）未变且目标文件仍在，
直接复用上次的计划跳过昂贵的 AI 匹配流程（DSH 会话），秒级完成幂等落盘。

设计约定：
- 纯标准库，不依赖 deepseek-harness-sdk；
- 单进程内多个 worker 线程共享一个实例，get/put 由锁保护；
- 持久化采用原子写入（临时文件 + os.replace），损坏/缺失/非法条目一律视为"无缓存"，
  绝不因索引问题中断整理流程；
- 条目只记录"计划覆盖且成功落盘"的文件（指纹 = 这些文件排序后的相对路径）。
  未覆盖而镜像进兜底目录的文件**不记录**：fallback 意味着未真正匹配，重打标签可以重试 AI。
"""

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeGuard

from logger import get_logger

logger = get_logger(__name__)

FORMAT_VERSION = 1


class OrganizeIndex:
    """按 torrent hash 记录整理结果的进程内 + 文件持久化索引。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._torrents: dict[str, dict[str, Any]] = {}
        self._load()

    def get(self, torrent_hash: str) -> dict | None:
        """返回校验过的条目；无记录或条目非法 → None（视为未缓存）。"""
        with self._lock:
            entry = self._torrents.get(torrent_hash)
        if entry is None:
            return None
        if not self._valid(entry):
            logger.warning("Invalid cached entry for torrent %s, ignoring", torrent_hash[:8])
            return None
        return entry

    def put(self, torrent_hash: str, entry: dict) -> None:
        """用 timestamp 标记并持久化条目；条目非法时抛 ValueError（编程错误，测试兜底）。"""
        record = dict(entry)
        record["ts"] = datetime.now(UTC).isoformat()
        if not self._valid(record):
            raise ValueError("refusing to index invalid entry")
        with self._lock:
            self._torrents[torrent_hash] = record
            self._save()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning("Failed to load organize index %s (%s) — treating as empty", self.path, e)
            return

        if not isinstance(raw, dict) or raw.get("version") != FORMAT_VERSION:
            logger.warning("Unsupported organize index format in %s — treating as empty", self.path)
            return
        torrents = raw.get("torrents")
        if not isinstance(torrents, dict):
            logger.warning("Malformed organize index %s — treating as empty", self.path)
            return

        dropped = 0
        for key, entry in torrents.items():
            if isinstance(key, str) and self._valid(entry):
                self._torrents[key] = entry
            else:
                dropped += 1
        if dropped:
            word = "entry" if dropped == 1 else "entries"
            logger.warning("Dropped %d invalid %s from organize index %s", dropped, word, self.path)

    def _save(self) -> None:
        data = {"version": FORMAT_VERSION, "torrents": self._torrents}
        tmp = self.path.with_name(self.path.name + ".tmp")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    @staticmethod
    def _valid(entry: object) -> bool:
        """条目 schema 校验：指纹/文件列表/目标路径三者严格一致。"""
        if not isinstance(entry, dict):
            return False
        if entry.get("kind") not in ("movie", "tv"):
            return False
        title = entry.get("title")
        if not isinstance(title, str) or not title.strip():
            return False
        year = entry.get("year")
        if not _is_int(year) or not 1900 <= year <= 2100:
            return False
        tmdb_id = entry.get("tmdb_id")
        if not _is_int(tmdb_id) or tmdb_id <= 0:
            return False

        fingerprint = entry.get("fingerprint")
        if not isinstance(fingerprint, list) or not all(isinstance(f, str) and f for f in fingerprint):
            return False

        dests = entry.get("dests")
        if not isinstance(dests, dict) or not all(
            isinstance(k, str) and isinstance(v, str) and v for k, v in dests.items()
        ):
            return False
        if sorted(dests) != sorted(fingerprint):
            return False

        files = entry.get("files")
        if not isinstance(files, list):
            return False
        if sorted((f.get("file") for f in files if isinstance(f, dict)), key=str) != sorted(fingerprint):
            return False
        for f in files:
            if not isinstance(f, dict):
                return False
            rel = f.get("file")
            if not isinstance(rel, str) or rel not in fingerprint:
                return False
            season = f.get("season")
            if season is not None and (not _is_int(season) or season < 0):
                return False
            episodes = f.get("episodes")
            if episodes is not None and (
                not isinstance(episodes, list) or not all(_is_int(e) and e >= 1 for e in episodes)
            ):
                return False
            episode_title = f.get("episode_title")
            if episode_title is not None and (not isinstance(episode_title, str) or not episode_title.strip()):
                return False
        return True


def _is_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)
