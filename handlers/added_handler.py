from typing import List

from qbittorrentapi import TorrentDictionary

from handlers.base_handler import BaseHandler


class AddedHandler(BaseHandler):
    def handle(self, task: TorrentDictionary) -> None:
        short_hash = task.hash[:8]
        self.logger.info(f"[ADDED] 📥 Processing '{task.name}' ({short_hash})")

        # 跳过空种子或单文件种子（无选择必要）
        files = task.files
        if not files or len(files) <= 1:
            self.logger.debug(
                "    → Skipping: empty, single-file, or metadata-only torrent"
            )
            self._cleanup_processing_tag(task.hash)
            return

        self.logger.debug(f"    → Inspecting {len(files)} files against skip rules...")

        # 构建 id → name 映射，避免后续重复遍历
        file_id_to_name = {f.id: f.name for f in files}

        files_to_disable: List[int] = [
            f.id for f in files if f.priority != 0 and self._match_rule(f.name)
        ]

        if not files_to_disable:
            self.logger.debug(f"    → No files matched skip rules in '{task.name}'")
            self._cleanup_processing_tag(task.hash)
            return

        # 执行禁用下载
        try:
            self.client.set_file_no_download(hash=task.hash, file_ids=files_to_disable)
            self.logger.info(
                f"    ✅ Skipped {len(files_to_disable)}/{len(files)} files "
                f"for '{task.name}'"
            )
            # DEBUG: 列出被跳过的文件名
            for fid in files_to_disable:
                self.logger.debug(
                    f"      - Disabled: {file_id_to_name.get(fid, '<?>')}"
                )
        except Exception as e:
            self.logger.error(f"    ❌ Failed to disable files for {short_hash}: {e}")
        finally:
            self._cleanup_processing_tag(task.hash)
