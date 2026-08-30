"""organize 触发标签处理器：AI 识别匹配 + Jellyfin 命名 + 硬链接/拷贝落盘。

分工：
- AI（:class:`ai_matcher.DeepSeekMatcher`）负责发布名识别与 TMDB 元数据匹配；
- 本处理器负责确定性执行：按 Jellyfin 官方命名模板生成目标路径，逐文件硬链接
  （跨文件系统等任何失败回退拷贝），并处理幂等重试与未匹配兜底。
"""

import os
import shutil
from pathlib import Path

from qbittorrentapi import TorrentDictionary

from ai_matcher import DeepSeekMatcher, MatchError, MatchPlan
from handlers.base_handler import BaseHandler
from logger import ContextFilter
from media_naming import episode_destination, movie_destination

DEFAULT_VIDEO_EXTENSIONS = [".mp4", ".mkv", ".avi", ".ts", ".m2ts", ".wmv", ".iso"]


class OrganizeHandler(BaseHandler):
    def __init__(self, client, matcher: DeepSeekMatcher, cfg: dict):
        super().__init__(client, [])
        self.matcher = matcher
        self.movies_dir = Path(cfg["library"]["movies_dir"])
        self.tv_dir = Path(cfg["library"]["tv_dir"])
        self.fallback_dir = Path(cfg["library"]["fallback_dir"])
        self.on_exists = cfg.get("on_exists", "skip")
        self.on_match_failure = cfg.get("on_match_failure", "fallback")
        self.min_file_size = cfg.get("min_file_size_mb", 0) * 1024 * 1024
        self.include_episode_title = cfg.get("include_episode_title", False)
        self.include_tmdb_id = cfg.get("include_tmdb_id", True)
        extensions = cfg.get("video_extensions") or DEFAULT_VIDEO_EXTENSIONS
        self.video_extensions = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in extensions}

    def handle(self, task: TorrentDictionary) -> None:
        short_hash = task.hash[:8]
        ContextFilter.set(
            operation="organize",
            torrent_hash=short_hash,
            torrent_name=task.name,
        )
        self.logger.info("[ORGANIZE] Processing '%s' (%s)", task.name, short_hash)
        try:
            self._organize(task)
        finally:
            self._cleanup_processing_tag(task.hash)

    def _organize(self, task: TorrentDictionary) -> None:
        progress = task.progress if task.progress is not None else 0.0
        if progress < 1:
            # 未完成不整理：触发标签保留，下轮重试（触发路径应为 completed 之后）
            raise RuntimeError(f"torrent not complete (progress={progress:.2f})")

        eligible = self._eligible_files(task)
        if not eligible:
            # completed 清理可能已删除全部内容：无可整理文件视为完成
            self.logger.warning("No eligible files to organize for '%s'", task.name)
            return

        context = {
            "hash": task.hash,
            "name": task.name,
            "category": task.get("category") or "",
            "files": [{"file": rel, "size": size} for rel, size in eligible],
        }

        try:
            plan = self.matcher.match(context)
            self._apply_plan(task, eligible, plan)
        except MatchError as e:
            self._handle_match_failure(task, eligible, e)

    def _eligible_files(self, task: TorrentDictionary) -> list[tuple[str, int]]:
        """过滤：视频后缀 + 最小大小 + 磁盘存在（priority=0 或已被清理的文件不存在）。"""
        save_path = Path(task.save_path)
        eligible: list[tuple[str, int]] = []
        for f in task.files or []:
            rel = f.name
            if Path(rel).suffix.lower() not in self.video_extensions:
                self.logger.debug("Skipping non-video file: %s", rel)
                continue
            size = int(getattr(f, "size", 0) or 0)
            if size < self.min_file_size:
                self.logger.debug("Skipping file below min size: %s (%d B)", rel, size)
                continue
            if not (save_path / rel).is_file():
                self.logger.debug("Skipping missing file on disk: %s", rel)
                continue
            eligible.append((rel, size))
        return eligible

    def _apply_plan(self, task: TorrentDictionary, eligible: list[tuple[str, int]], plan: MatchPlan) -> None:
        save_path = Path(task.save_path)
        planned = {pf.file: pf for pf in plan.files}
        stats = {"link": 0, "copy": 0, "skip": 0}

        for index, pf in enumerate(plan.files):
            src = save_path / pf.file
            if not src.is_file():
                self.logger.warning("Planned file missing on disk, skipping: %s", pf.file)
                continue
            suffix = src.suffix
            if plan.kind == "movie":
                # 同一种子多个视频文件 → Jellyfin 多段命名 -cd1/-cd2...
                part = index + 1 if len(plan.files) > 1 else None
                _dir, dest_base = movie_destination(
                    self.movies_dir,
                    plan.title,
                    plan.year,
                    plan.tmdb_id,
                    self.include_tmdb_id,
                    part=part,
                )
            else:
                _dir, dest_base = episode_destination(
                    self.tv_dir,
                    plan.title,
                    plan.year,
                    plan.tmdb_id,
                    pf.season or 0,
                    pf.episodes,
                    pf.episode_title,
                    self.include_tmdb_id,
                    self.include_episode_title,
                )
            action = self._place_file(src, Path(str(dest_base) + suffix))
            stats[action] += 1
            if action == "link":
                self.logger.debug("Hardlinked %s → %s", pf.file, dest_base)
            elif action == "copy":
                self.logger.warning("Fallback copy %s → %s", pf.file, dest_base)

        unplanned = [rel for rel, _size in eligible if rel not in planned]
        if unplanned:
            self.logger.warning(
                "%d file(s) not covered by AI plan, mirroring to fallback dir: %s",
                len(unplanned),
                unplanned,
            )
            self._mirror_files(task, unplanned)

        self.logger.info(
            "Organized '%s' → %s '%s' (%d) [tmdbid-%d] | link=%d copy=%d skip=%d",
            task.name,
            plan.kind,
            plan.title,
            plan.year,
            plan.tmdb_id,
            stats["link"],
            stats["copy"],
            stats["skip"],
        )

    def _handle_match_failure(self, task: TorrentDictionary, eligible: list[tuple[str, int]], error: Exception) -> None:
        self.logger.warning("AI match failed for '%s' (%s): %s", task.name, task.hash[:8], error)
        if self.on_match_failure == "fail":
            raise error
        self._mirror_files(task, [rel for rel, _size in eligible])

    def _mirror_files(self, task: TorrentDictionary, rel_paths: list[str]) -> None:
        """兜底：镜像原始相对路径进 fallback_dir（逐文件，规避共享根目录问题）。"""
        save_path = Path(task.save_path)
        for rel in rel_paths:
            src = save_path / rel
            if not src.is_file():
                self.logger.debug("Fallback source missing on disk: %s", rel)
                continue
            dest = self.fallback_dir / rel
            self._place_file(src, dest)

    def _place_file(self, src: Path, dest: Path) -> str:
        """硬链接，任何失败回退拷贝；返回 'link' | 'copy' | 'skip'。"""
        try:
            if dest.resolve() == src.resolve():
                self.logger.debug("Source and destination are identical, skipping: %s", src)
                return "skip"
        except OSError:
            pass

        if dest.exists():
            if self._same_file(src, dest):
                return "skip"  # 幂等：重试时已落盘文件直接跳过
            if self.on_exists == "skip":
                self.logger.debug("Destination exists, skipping (on_exists=skip): %s", dest)
                return "skip"
            try:
                dest.unlink()
            except OSError as e:
                raise RuntimeError(f"failed to replace existing destination {dest}: {e}") from e

        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.link(src, dest)
            return "link"
        except OSError as e:
            self.logger.debug("Hardlink failed for %s → %s (%s), falling back to copy", src, dest, e)
            shutil.copy2(src, dest)
            return "copy"

    @staticmethod
    def _same_file(a: Path, b: Path) -> bool:
        try:
            stat_a = os.stat(a)
            stat_b = os.stat(b)
        except OSError:
            return False
        return (stat_a.st_dev, stat_a.st_ino) == (stat_b.st_dev, stat_b.st_ino)
