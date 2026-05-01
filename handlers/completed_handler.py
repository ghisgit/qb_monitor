import os
import shutil
from pathlib import Path

from qbittorrentapi import TorrentDictionary

from handlers.base_handler import BaseHandler
from logger import ContextFilter


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
            self.logger.debug(
                "Skipping single-file torrent: %s", content_path.name
            )
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
            self.logger.warning(
                "Content path missing for '%s': %s", torrent_name, path
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
                        "Deleted unwanted file (priority=0): %s", f.name
                    )
                except OSError as e:
                    self.logger.error("Failed to delete %s: %s", file_path, e)

    def _clean_matching_items(self, content_path: Path) -> int:
        if not content_path.is_dir():
            self.logger.warning("Expected directory but got: %s", content_path)
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
                "Failed to scan directory tree %s: %s", content_path, e
            )
            return 0

        if all_paths:
            self.logger.debug("Found %d items matching rules", len(all_paths))

        for path in all_paths:
            try:
                if path.exists():
                    if path.is_dir():
                        shutil.rmtree(path)
                        self.logger.debug("Deleted dir (rule match): %s", path)
                    else:
                        path.unlink()
                        self.logger.debug("Deleted file (rule match): %s", path)
                    deleted_count += 1
            except OSError as e:
                self.logger.error("Failed to delete %s: %s", path, e)

        try:
            for root, dirs, files in os.walk(content_path, topdown=False):
                for d in dirs:
                    dir_path = Path(root) / d
                    try:
                        if dir_path.exists() and not any(dir_path.iterdir()):
                            dir_path.rmdir()
                            self.logger.debug(
                                "Deleted empty dir (recursive): %s", dir_path
                            )
                            deleted_count += 1
                    except OSError:
                        continue
        except OSError as e:
            self.logger.error("Error during recursive empty-dir cleanup: %s", e)

        return deleted_count
