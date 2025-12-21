#!/bin/bash
# qBittorrent 添加种子时调用
# 参数: %K: Torrent ID

# 获取脚本所在目录（兼容 source 或 symlink）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$SCRIPT_DIR/logs/added_tag.log"

# 日志函数
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"
}

TORRENT_ID="$1"
TAG="added"
URL="http://127.0.0.1:8080/api/v2/torrents/addTags"
MAX_RETRIES=99
RETRY_DELAY=2

# 输入校验
if [[ -z "$TORRENT_ID" ]]; then
    log "错误：未提供种子哈希值。用法: $0 <torrent_id>"
    echo "错误：请提供种子哈希值。" >&2
    exit 1
fi

log "开始为种子 $TORRENT_ID 添加标签 '$TAG'"

for ((i=1; i<=MAX_RETRIES; i++)); do
    log "尝试第 $i 次添加标签..."
    
    if curl -s -f -d "hashes=$TORRENT_ID&tags=$TAG" "$URL" >> "$LOG_FILE" 2>&1; then
        log "✅ 标签添加成功！"
        echo "✅ 标签添加成功！详情见 $LOG_FILE"
        exit 0
    else
        exit_code=$?
        log "❌ 第 $i 次尝试失败（curl 退出码: $exit_code）"
        if [ $i -lt $MAX_RETRIES ]; then
            log "等待 ${RETRY_DELAY} 秒后重试..."
            sleep $RETRY_DELAY
        fi
    fi
done

log "⚠️ 重试 $MAX_RETRIES 次后仍然失败，放弃操作。"
echo "⚠️ 操作失败，请查看日志：$LOG_FILE" >&2
exit 1