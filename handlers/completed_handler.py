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
        for f in task.files:
            if f.priority != 0:
                continue
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
        if not content_path.is_dir():
            self.logger.warning(f"    ⚠️ Expected directory but got: {content_path}")
            return 0

        deleted_count = 0

        try:
            all_paths = []
            for root, dirs, files in os.walk(content_path, topdown=False):
                for f in files:
                    if self._match_rule(f):
                        all_paths.append(Path(root) / f)
                for d in dirs:
                    if self._match_rule(d):
                        all_paths.append(Path(root) / d)
        except OSError as e:
            self.logger.error(
                f"    ❌ Failed to scan directory tree {content_path}: {e}"
            )
            return 0

        if all_paths:
            self.logger.debug(f"    → Found {len(all_paths)} items matching rules")

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
                        continue
        except OSError as e:
            self.logger.error(f"    ❌ Error during recursive empty-dir cleanup: {e}")

        return deleted_count
