import shutil
from pathlib import Path
from models import TorrentTask
from handlers.base_handler import BaseHandler


class CompletedHandler(BaseHandler):
    def handle(self, task: TorrentTask) -> None:
        short_hash = task.hash[:8]
        self.logger.info(f"[COMPLETED] 🧹 Cleaning '{task.name}' ({short_hash})")

        content_path = Path(task.content_path)

        # 检查路径是否存在
        if not content_path.exists():
            self.logger.warning(
                f"    ⚠️ Content path missing for '{task.name}': {content_path}"
            )
            return

        # 单文件种子：无目录内容可清理，直接跳过
        if content_path.is_file():
            self.logger.debug(
                f"    → Skipping single-file torrent: {content_path.name}"
            )
            self._cleanup_processing_tag(task.hash)
            return

        # 扫描目录内容
        try:
            entries = list(content_path.iterdir())
        except OSError as e:
            self.logger.error(f"    ❌ Cannot read directory {content_path}: {e}")
            return

        deleted_count = 0
        total_items = len(entries)
        self.logger.debug(f"    → Scanning {total_items} items in {content_path}")

        for entry in entries:
            if self._match_rule(entry.name):
                try:
                    if entry.is_dir():
                        shutil.rmtree(entry)
                        self.logger.debug(f"      🗑️ Deleted dir: {entry.name}")
                    else:
                        entry.unlink()
                        self.logger.debug(f"      🗑️ Deleted file: {entry.name}")
                    deleted_count += 1
                except OSError as e:
                    self.logger.error(f"      ❌ Failed to delete {entry}: {e}")

        # 输出清理摘要
        if deleted_count:
            self.logger.info(
                f"    ✅ Cleaned {deleted_count}/{total_items} items in '{task.name}'"
            )
        else:
            self.logger.debug(f"    → No items matched deletion rules in '{task.name}'")

        # 清理标签
        self._cleanup_processing_tag(task.hash)
