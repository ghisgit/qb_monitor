import os
import shutil
from pathlib import Path

from qbittorrentapi import TorrentDictionary

from handlers.base_handler import BaseHandler


class CompletedHandler(BaseHandler):
    def handle(self, task: TorrentDictionary) -> None:
        short_hash = task.hash[:8]
        self.logger.info(f"[COMPLETED] 🧹 Cleaning '{task.name}' ({short_hash})")

        content_path = Path(task.content_path)
        save_path = Path(task.save_path)
        if content_path == save_path:
            self.logger.debug("    → Skipping: single-file torrent")
            self._cleanup_processing_tag(task.hash)
            return

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
        递归清理 content_path 下所有匹配跳过规则的文件和目录，
        然后递归删除所有因此产生的空目录（包括多层嵌套）。
        返回删除的总项数（文件 + 目录）。
        """
        if not content_path.is_dir():
            self.logger.warning(f"    ⚠️ Expected directory but got: {content_path}")
            return 0

        deleted_count = 0

        # 第一步：递归收集所有匹配规则的路径（深度优先，先子后父）
        try:
            # 使用 os.walk(topdown=False) 便于后续安全删除（先删深层）
            all_paths = []
            for root, dirs, files in os.walk(content_path, topdown=False):
                # 先处理文件
                for f in files:
                    file_path = Path(root) / f
                    if self._match_rule(f):
                        all_paths.append(file_path)
                # 再处理目录（注意：此时子目录可能已被标记删除，但没关系）
                for d in dirs:
                    dir_path = Path(root) / d
                    if self._match_rule(d):
                        all_paths.append(dir_path)
        except OSError as e:
            self.logger.error(
                f"    ❌ Failed to scan directory tree {content_path}: {e}"
            )
            return 0

        if not all_paths:
            self.logger.debug(
                f"    → No items matched deletion rules in {content_path}"
            )
            # 即便没有匹配项，仍需检查是否全空？→ 由第二步处理
        else:
            self.logger.debug(f"    → Found {len(all_paths)} items matching rules")

        # 删除所有匹配项
        for path in all_paths:
            try:
                if path.exists():
                    if path.is_dir():
                        shutil.rmtree(path)
                        self.logger.debug(f"      🗑️ Deleted dir (rule match): {path}")
                    else:
                        path.unlink()
                        self.logger.debug(f"      🗑️ Deleted file (rule match): {path}")
                    deleted_count += 1
            except OSError as e:
                self.logger.error(f"      ❌ Failed to delete {path}: {e}")

        # 第二步：递归删除所有空目录（自底向上）
        try:
            for root, dirs, files in os.walk(content_path, topdown=False):
                for d in dirs:
                    dir_path = Path(root) / d
                    try:
                        if dir_path.exists() and not any(dir_path.iterdir()):
                            dir_path.rmdir()
                            self.logger.debug(
                                f"      🗑️ Deleted empty dir (recursive): {dir_path}"
                            )
                            deleted_count += 1
                    except OSError:
                        # 被占用、不存在或刚写入 —— 安静跳过
                        continue
        except OSError as e:
            self.logger.error(f"    ❌ Error during recursive empty-dir cleanup: {e}")

        return deleted_count
