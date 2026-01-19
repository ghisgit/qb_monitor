import os
import shutil
from pathlib import Path
from typing import List

from qbittorrentapi import TorrentDictionary

from handlers.base_handler import BaseHandler


class CompletedHandler(BaseHandler):
    def handle(self, task: TorrentDictionary) -> None:
        short_hash = task.hash[:8]
        self.logger.info(f"[COMPLETED] 🧹 Cleaning '{task.name}' ({short_hash})")

        content_path = Path(task.content_path)

        if not self._ensure_content_path_exists(content_path, task.name):
            self._cleanup_processing_tag(task.hash)
            return

        if content_path.is_file():
            self.logger.debug(
                f"    → Skipping single-file torrent: {content_path.name}"
            )
            self._cleanup_processing_tag(task.hash)
            return

        self._delete_unwanted_files(task)

        deleted_count = self._clean_matching_items(content_path)

        # 输出摘要
        if deleted_count > 0:
            self.logger.info(f"    ✅ Cleaned {deleted_count} items in '{task.name}'")
        else:
            self.logger.debug(f"    → No items matched deletion rules in '{task.name}'")

        self._cleanup_processing_tag(task.hash)

    def _ensure_content_path_exists(self, path: Path, torrent_name: str) -> bool:
        if not path.exists():
            self.logger.warning(
                f"    ⚠️ Content path missing for '{torrent_name}': {path}"
            )
            return False
        return True

    def _delete_unwanted_files(self, task: TorrentDictionary) -> None:
        """删除 priority=0 的文件（仅当存在）"""
        for f in task.files:
            if f.priority == 0:
                # f.name是相对路径，相对于保存路径(save_path)
                file_path = Path(task.save_path) / f.name
                if file_path.exists():
                    try:
                        file_path.unlink()
                        self.logger.debug(
                            f"      🗑️ Deleted unwanted file (priority=0): {f.name}"
                        )
                    except OSError as e:
                        self.logger.error(f"      ❌ Failed to delete {file_path}: {e}")

    def _clean_matching_items(self, content_path: Path) -> int:
        """
        清理匹配跳过规则的文件/目录，并递归删除所有空子目录（包括多层嵌套）。
        返回删除的总项数（文件 + 目录）。
        """
        if not content_path.is_dir():
            self.logger.warning(f"    ⚠️ Expected directory but got: {content_path}")
            return 0

        deleted_count = 0

        # 第一步：扫描并删除直接匹配规则的文件和目录（仅一级）
        try:
            direct_entries: List[Path] = list(content_path.iterdir())
        except OSError as e:
            self.logger.error(f"    ❌ Cannot read directory {content_path}: {e}")
            return 0

        if not direct_entries:
            self.logger.debug(f"    → Directory is empty: {content_path}")
            return 0

        self.logger.debug(
            f"    → Scanning {len(direct_entries)} items in {content_path}"
        )

        for entry in direct_entries:
            if self._match_rule(entry.name):
                try:
                    if entry.is_dir():
                        shutil.rmtree(entry)
                        self.logger.debug(
                            f"      🗑️ Deleted dir (rule match): {entry.name}"
                        )
                    else:
                        entry.unlink()
                        self.logger.debug(
                            f"      🗑️ Deleted file (rule match): {entry.name}"
                        )
                    deleted_count += 1
                except OSError as e:
                    self.logger.error(f"      ❌ Failed to delete {entry}: {e}")

        # 第二步：递归删除所有空子目录（自底向上）
        try:
            # topdown=False 确保从最深的子目录开始处理
            for root, dirs, files in os.walk(content_path, topdown=False):
                for d in dirs:
                    dir_path = Path(root) / d
                    try:
                        # 检查是否为空（且存在）
                        if dir_path.exists() and not any(dir_path.iterdir()):
                            dir_path.rmdir()
                            self.logger.debug(
                                f"      🗑️ Deleted empty dir (recursive): {dir_path}"
                            )
                            deleted_count += 1
                    except OSError:
                        # 目录可能已被删除、被占用，或刚有新文件写入 —— 安静跳过
                        continue
        except OSError as e:
            self.logger.error(f"    ❌ Error during recursive empty-dir cleanup: {e}")

        return deleted_count
