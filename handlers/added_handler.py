from qbittorrentapi import TorrentDictionary

from handlers.base_handler import BaseHandler
from logger import ContextFilter


class AddedHandler(BaseHandler):
    def handle(self, task: TorrentDictionary) -> None:
        short_hash = task.hash[:8]
        ContextFilter.set(
            operation="added",
            torrent_hash=short_hash,
            torrent_name=task.name,
        )
        self.logger.info("[ADDED] Processing '%s' (%s)", task.name, short_hash)

        files = task.files
        if not files or len(files) <= 1:
            self.logger.debug("Skipping: empty, single-file, or metadata-only torrent")
            self._cleanup_processing_tag(task.hash)
            return

        self.logger.debug("Inspecting %d files against skip rules...", len(files))

        file_id_to_name = {f.id: f.name for f in files}

        files_to_disable: list[int] = [f.id for f in files if f.priority != 0 and self._match_rule(f.name)]

        if not files_to_disable:
            self.logger.debug("No files matched skip rules in '%s'", task.name)
            self._cleanup_processing_tag(task.hash)
            return

        try:
            self.client.set_file_no_download(torrent_hash=task.hash, file_ids=files_to_disable)
            self.logger.info(
                "Skipped %d/%d files for '%s'",
                len(files_to_disable),
                len(files),
                task.name,
            )
            for fid in files_to_disable:
                self.logger.debug("Disabled: %s", file_id_to_name.get(fid, "<? >"))
        except Exception as e:
            self.logger.error("Failed to disable files for %s: %s", short_hash, e)
        finally:
            self._cleanup_processing_tag(task.hash)
