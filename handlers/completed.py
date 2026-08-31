import os
import shutil
from pathlib import Path

from qbittorrentapi import TorrentDictionary

from core.logger import ContextFilter
from handlers.base import BaseHandler


class CompletedHandler(BaseHandler):
    def handle(self, task: TorrentDictionary) -> None:
        short_hash = task.hash[:8]
        ContextFilter.set(
            operation="completed",
            torrent_hash=short_hash,
            torrent_name=task.name,
        )
        self.logger.info("[COMPLETED] Cleaning '%s' (%s)", task.name, short_hash)

        content_path = Path(task.content_path)
        save_path = Path(task.save_path)
        if content_path == save_path:
            self.logger.debug("Skipping: single-file torrent")
            self._cleanup_processing_tag(task.hash)
            return

        if not self._ensure_content_path_exists(content_path, task.name):
            self._cleanup_processing_tag(task.hash)
            return

        if content_path.is_file():
            self.logger.debug("Skipping single-file torrent: %s", content_path.name)
            self._cleanup_processing_tag(task.hash)
            return

        self._delete_unwanted_files(task)

        deleted_count = self._clean_matching_items(content_path)

        if deleted_count > 0:
            self.logger.info("Cleaned %d items in '%s'", deleted_count, task.name)
        else:
            self.logger.debug("No items matched deletion rules in '%s'", task.name)

        self._cleanup_processing_tag(task.hash)

    def _ensure_content_path_exists(self, path: Path, torrent_name: str) -> bool:
        if not path.exists():
            self.logger.warning("Content path missing for '%s': %s", torrent_name, path)
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
                    self.logger.debug("Deleted unwanted file (priority=0): %s", f.name)
                except OSError as e:
                    self.logger.error("Failed to delete %s: %s", file_path, e)

    def _clean_matching_items(self, content_path: Path) -> int:
        if not content_path.is_dir():
            self.logger.warning("Expected directory but got: %s", content_path)
            return 0

        deleted_count = 0

        try:
            for root, dirs, files in os.walk(content_path, topdown=False):
                for f in files:
                    fp = Path(root) / f
                    if self._match_rule(f):
                        try:
                            fp.unlink()
                            self.logger.debug("Deleted file (rule match): %s", fp)
                            deleted_count += 1
                        except OSError as e:
                            self.logger.error("Failed to delete %s: %s", fp, e)
                for d in dirs:
                    dp = Path(root) / d
                    if self._match_rule(d):
                        try:
                            shutil.rmtree(dp)
                            self.logger.debug("Deleted dir (rule match): %s", dp)
                            deleted_count += 1
                        except OSError as e:
                            self.logger.error("Failed to delete %s: %s", dp, e)
                    else:
                        try:
                            if dp.exists() and not any(dp.iterdir()):
                                dp.rmdir()
                                self.logger.debug("Deleted empty dir (recursive): %s", dp)
                                deleted_count += 1
                        except OSError:
                            continue
        except OSError as e:
            self.logger.error("Failed to scan directory tree %s: %s", content_path, e)

        return deleted_count
