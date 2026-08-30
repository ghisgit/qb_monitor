# QB Monitor

qBittorrent 自动化监控与清理工具。基于标签驱动的事件处理架构，自动跳过不需要的文件、清理已完成种子的冗余数据，并监控卡顿种子进行优先级降级。

## 功能特点

- **添加时自动跳过文件** — 种子添加后，根据正则规则自动将匹配文件设为不下载（如样本图、NFO、字幕等）
- **完成后自动清理** — 种子下载完成后，自动删除未下载的文件（priority=0）及匹配清理规则的文件和空目录
- **AI 整理入库（可选）** — 打上 `organize` 触发标签的种子，由 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) agent 识别发布名并匹配 TMDB 元数据（[deepseek-harness-sdk](https://pypi.org/project/deepseek-harness-sdk/) 驱动），按 [Jellyfin 官方推荐目录结构](https://jellyfin.org/docs/general/server/media/)硬链接到媒体库；跨文件系统等硬链接失败自动回退拷贝，识别失败镜像进兜底目录不丢文件
- **卡顿种子降级** — 统一监控 `metaDL`/`stalledDL`/`downloading`/`forcedDL` 四种状态，活跃下载数超阈值时自动将长时间卡顿种子降至队列底部
- **异常恢复** — 启动时自动恢复上次异常退出后卡在 `processing` 状态的种子，避免任务丢失
- **优雅退出** — Ctrl+C 时等待队列中任务完成后再退出
- **高可靠 API 调用** — 所有 qBittorrent API 调用均带指数退避重试机制

## 技术栈

| 组件            | 技术                                                            |
| --------------- | --------------------------------------------------------------- |
| 语言            | Python 3.14+                                                    |
| API 客户端      | [qbittorrent-api](https://github.com/rmartin16/qbittorrent-api) |
| AI 驱动         | [deepseek-harness-sdk](https://pypi.org/project/deepseek-harness-sdk/)（仅 Linux/macOS，无 Windows wheel） |
| 配置解析        | PyYAML                                                          |
| 容器化          | Docker (python:3.14-slim-bookworm + uv)                         |
| 并发模型        | threading + queue (生产者-消费者模式)                           |

## 适用场景

- PT/BT 站点批量下载，需要自动过滤附属文件
- 下载完成后自动清理不需要的样本、广告文件
- 大量种子同时下载时，需要自动管理卡顿种子
- 7×24 小时无人值守运行

## 工作流程

```text
qBittorrent 添加种子
        │
        ▼
  Shell 脚本打标签 ──→ added
        │                    │
        │     Orchestrator 轮询检测（命中任一触发标签
        │        且 有元数据 且 无 processing）
        │                    │
        │              批量添加 processing 标签
        │              统一放入任务队列（不移除触发标签）
        │                    │
        │         ┌──────────┴──────────┐
        │         ▼                     ▼
        │   AddedHandler          (下载中...)
        │   跳过匹配规则的文件
        │         │
        │         │              qBittorrent 下载完成
        │         │                     │
        │         │              Shell 脚本打标签 ──→ completed
        │         │                                  │
        │         │        Orchestrator 轮询检测（同上，统一入队）
        │         │                                  │
        │         │                                  ▼
        │         │                          CompletedHandler
        │         │                          删除 priority=0 文件
        │         │                          删除匹配清理规则文件
        │         │                          递归清理空目录
        │         │                                  │
        │         └──────────────────────────────────┘
        │                     │
        │          handler 成功后由 Orchestrator
        │          移除触发标签（失败则保留，下轮重试）
        │                     │
        │          Post 链（仅 enable_post_chain 标签）
        │          CategoryTagHandler 按动作×分类正则补打标签
        │          （如 completed + ^tv → 打 organize 标签）
        │                     │
        │                     ▼
        │          OrganizeHandler（触发标签 organize，可配置）
        │          DeepSeek Harness agent 识别发布名、
        │          匹配 TMDB 元数据 → Jellyfin 推荐目录结构
        │          逐文件硬链接（失败回退拷贝）
        │                     │
        ▼                     ▼
       完成 ✅
```

## 环境要求

- qBittorrent 4.6.0+ (开启 Web UI)
- Docker (推荐) 或 Python 3.14+
- qBittorrent 4.6.0+ (Web UI，用于 autorun 标签触发)
- 启用 organize 时：DeepSeek API Key（`DEEPSEEK_API_KEY`）、TMDB API Key、可访问 api.themoviedb.org 与 DeepSeek 端点；仅支持 Linux/macOS（无 Windows runtime wheel）

## 安装与配置

### 1. 克隆项目

```bash
git clone <repository-url> qb_monitor
cd qb_monitor
```

### 2. 创建配置文件

基于模板创建配置文件：

```bash
cp template_config.yaml config.yaml
```

编辑 `config.yaml`：

```yaml
rules:
  added:
    - "(\\.nfo|\\.txt|\\.jpg|\\.png|\\.url)$"
    - "sample"
  completed:
    - "(\\.nfo|\\.txt|\\.jpg|\\.png|\\.url)$"
    - "sample"

qbittorrent:
  host: "http://127.0.0.1:8080"
  username: "admin"
  password: "adminadmin"

client:
  connect_timeout: 5
  read_timeout: 30
  retry:
    max_attempts: 3
    delay: 1.0
    backoff: 1.0
    cap: 30.0
  circuit_breaker:
    failure_threshold: 5
    recovery_timeout: 30.0

processor:
  poll_interval_seconds: 30
  stall_timeout_hours: 1
  max_worker_threads: 3

logging:
  logfile: "logs/qb_auto.log"
  level: "INFO"
  environment: "production"
  json_format: true
  rotation:
    max_file_size_mb: 10
    backup_count: 30
    retention_days: 30
    when: "midnight"
  sensitive_masking: true
```

### 3. 配置项说明

| 配置项                            | 类型      | 说明                                                      |
| --------------------------------- | --------- | --------------------------------------------------------- |
| `rules.added`                     | list[str] | 添加时文件名匹配规则（正则表达式），匹配的文件设为不下载  |
| `rules.completed`                 | list[str] | 完成后文件/目录名匹配规则（正则表达式），匹配的项将被删除 |
| `category_tags.added`             | dict[str, str \| list[str]] | 可选；added 动作完成后按 qB 分类与键名正则匹配（re.search，不区分大小写）补打标签，省略或为空则不启用 |
| `category_tags.completed`         | dict[str, str \| list[str]] | 可选；completed 动作完成后按 qB 分类与键名正则匹配（re.search，不区分大小写）补打标签，省略或为空则不启用 |
| `qbittorrent.host`                | str       | qBittorrent Web UI 地址                                   |
| `qbittorrent.username`            | str       | Web UI 用户名                                             |
| `qbittorrent.password`            | str       | Web UI 密码                                               |
| `processor.poll_interval_seconds` | int       | 轮询间隔（秒）                                            |
| `processor.stall_timeout_hours` | float     | 卡顿超时（小时），超过此时间种子将被降级                  |
| `processor.max_worker_threads`    | int       | 工作线程数                                                |
| `client.connect_timeout`          | number    | 连接超时（秒），默认 5                                    |
| `client.read_timeout`             | number    | 读取超时（秒），默认 30                                   |
| `client.retry.max_attempts`       | int       | API 调用最大重试次数，默认 3                              |
| `client.retry.delay`              | number    | 初始重试延迟（秒），默认 1.0                              |
| `client.retry.backoff`            | number    | 延迟倍增因子，默认 1.0                                    |
| `client.retry.cap`                | number    | 最大重试延迟上限（秒），默认 30.0                         |
| `client.circuit_breaker.failure_threshold` | int | 连续失败次数阈值，超过后熔断，默认 5              |
| `client.circuit_breaker.recovery_timeout` | number | 熔断后恢复等待时间（秒），默认 30.0              |
| `logging.level`                   | str       | 日志级别 (`DEBUG` / `INFO` / `WARNING` / `ERROR` / `FATAL`) |
| `logging.logfile`                 | str       | 日志文件路径                                              |
| `logging.environment`             | str       | 运行环境 (`development` / `testing` / `production`)，默认 `production` |
| `logging.json_format`             | bool      | 是否使用 JSON 格式输出日志，默认随 environment（生产为 true） |
| `logging.sensitive_masking`       | bool      | 是否脱敏 password/token/secret 等敏感信息，生产默认开启 |
| `logging.rotation.max_file_size_mb` | number   | 单个日志文件最大大小（MB），默认 10                      |
| `logging.rotation.backup_count`   | int       | 保留的备份文件数量，默认 30                               |
| `logging.rotation.retention_days` | int       | 日志文件保留天数，超期自动清理，默认 30                   |
| `logging.rotation.when`           | str       | 按时间轮转 (`midnight` / `H` / `D`)，默认 `midnight`      |
| `organize.enabled`                | bool      | 可选；是否启用 AI 整理处理器，默认 `false`                |
| `organize.tags`                   | list[str] | 整理触发标签（可多个，默认 `["organize"]`）               |
| `organize.library.movies_dir`     | str       | 电影媒体库根目录                                          |
| `organize.library.tv_dir`         | str       | 剧集媒体库根目录                                          |
| `organize.library.fallback_dir`   | str       | 识别失败/未匹配时的镜像兜底目录（保持种子原始结构）       |
| `organize.tmdb_api_key`           | str       | TMDB API Key（注入 agent 环境变量，不进入提示词）         |
| `organize.on_exists`              | str       | 目标已存在：`skip`（默认）/ `overwrite`                   |
| `organize.on_match_failure`       | str       | AI 匹配失败：`fallback`（默认，镜像进兜底目录）/ `fail`（保留触发标签报错重试） |
| `organize.ai_retries`             | int       | 单个种子 AI 瞬时失败进程内重试次数，默认 1                |
| `organize.min_file_size_mb`      | number    | 小于该大小的视频文件跳过，默认 0                          |
| `organize.include_episode_title`  | bool      | 文件名附加 TMDB 单集标题，默认 `false`                    |
| `organize.include_tmdb_id`        | bool      | 目录/文件名附加 `[tmdbid-xxxx]`，默认 `true`              |
| `organize.video_extensions`       | list[str] | 整理的文件后缀白名单                                      |
| `organize.dsh.model`              | str       | DeepSeek 模型 ID，默认 `deepseek-v4-flash`                |
| `organize.dsh.api_key`            | str       | 留空则使用环境变量 `DEEPSEEK_API_KEY`                     |
| `organize.dsh.base_url`           | str       | 留空则使用环境变量 `DEEPSEEK_BASE_URL`                    |
| `organize.dsh.language`           | str       | TMDB 搜索与标题本地化语言，默认 `zh-CN`                   |
| `organize.dsh.request_timeout_seconds` | number | 单个种子 AI 轮次超时（秒），默认 300                  |
| `organize.dsh.session_root`       | str       | agent 会话 JSONL 存储目录，默认 `sessions`                |

### 3.1 AI 整理处理器（organize）详解

**分工**：DeepSeek Harness agent（经 [deepseek-harness-sdk](https://pypi.org/project/deepseek-harness-sdk/) 驱动）负责识别发布名/文件名并调用 TMDB 匹配元数据；Python 侧只做确定性执行——校验 AI 输出的 JSON 计划、按 Jellyfin 官方推荐结构计算目标路径、逐文件硬链接（`os.link`，任何失败——如跨文件系统 `EXDEV`——自动回退 `shutil.copy2`）。**Python 从不执行 AI 输出的命令**，只消费通过 schema 校验的 JSON（`files[].file` 必须精确等于种子文件列表，阻断幻觉路径与路径穿越）。

**Jellyfin 目标路径**：

```text
电影: {movies_dir}/{Title} ({Year}) [tmdbid-{id}]/{Title} ({Year}) [tmdbid-{id}].ext
      一部种子多个视频 → -cd1/-cd2 后缀（Jellyfin 多段命名）
剧集: {tv_dir}/{Title} ({Year}) [tmdbid-{id}]/Season {NN}/{Title} ({Year}) - S{NN}E{EE}[-E{EE2}].ext
      多集合并文件 → S01E01-E02；S00 特典 → Season 00；可选项加 " - 单集标题"
```

`[tmdbid-xxxx]` 为 [Jellyfin 官方 provider-id 后缀](https://jellyfin.org/docs/general/server/metadata/identifiers/)，提高媒体识别准确率（`include_tmdb_id` 可关）。

**两个结构性问题**：

- 单文件与多文件目录种子统一「逐文件」处理：源文件 = `save_path + 相对路径`，无需特判；
- 多个种子共享根目录（如种子 A/B 均以 `ab` 为根，合并下载到 `/downloads/ab`）时，**绝不使用 `content_path` 作为整理来源**（两个种子报告同一路径会误带对方文件）；只迭代本种子 `task.files` 的条目，天然隔离。

**失败语义**：AI 瞬时失败（超时/输出不合法）按 `ai_retries` 进程内重试；重试耗尽或 TMDB 无匹配 → `on_match_failure: fallback`（默认）把文件镜像进 `fallback_dir`（保留原始相对路径，不丢文件）或 `fail`（触发标签保留、下轮重试）。落盘具备幂等性（inode 相同即跳过），部分完成后重试安全。

**依赖与限制**：

- `deepseek-harness-sdk` 附带 `deepseek-harness-runtime-bin`（约 210MB 的 bundled runtime），`uv sync` 自动安装；镜像体积相应增大
- runtime-bin **无 Windows wheel**（仅 Linux x86_64/aarch64、macOS arm64）：依赖带 `sys_platform != 'win32'` 标记，Windows 开发环境不受影响；Windows 上请保持 `organize.enabled: false`
- agent 经 Bash 调用 TMDB（`curl` 不可用时用 `python3` 标准库）；**TMDB API Key 经环境变量 `TMDB_API_KEY` 注入，不进入提示词**；DeepSeek 凭据走 `organize.dsh.api_key` 或 `DEEPSEEK_API_KEY` 环境变量
- agent 具备 Bash 执行能力（识别/查询用），提示词约束「不得修改文件系统」；生产环境建议运行在独立容器或仅挂载只读下载目录，以隔离信任边界
- 每种子一次 agent 轮次：延迟数秒至数十秒，`request_timeout_seconds` 兜底

### 4. 自动标签配置

启动时自动通过 API 设置 qBittorrent 的 autorun，为种子添加 `added` 和 `completed` 标签。**无需手动配置。**

```bash
# 等效的 autorun 命令（由 qb-monitor 自动设置）：
curl -s -f -d "hashes=%K&tags=added" http://127.0.0.1:8080/api/v2/torrents/addTags
curl -s -f -d "hashes=%K&tags=completed" http://127.0.0.1:8080/api/v2/torrents/addTags
```

## 运行

### Docker 运行（推荐）

镜像由 GitHub Actions 在每次 push 到 `main` 分支时自动构建并推送至 GitHub Container Registry（标签为 `latest` 和 git SHA），可直接拉取使用：

```bash
docker pull ghcr.io/ghisgit/qb_monitor:latest

docker run -d \
  --name qb-monitor \
  --restart unless-stopped \
  -e DEEPSEEK_API_KEY=sk-xxx \                 # 启用 organize 时需要
  -v /path/to/config.yaml:/app/config.yaml \
  -v /path/to/logs:/app/logs \
  -v /path/to/downloads:/downloads \            # 启用 organize 时挂载下载目录
  -v /path/to/media:/media \                    # 启用 organize 时挂载媒体库目录
  ghcr.io/ghisgit/qb_monitor:latest
```

> 注意：镜像包含 DeepSeek Harness bundled runtime（约 210MB），体积较大；organize 的 agent 会话 JSONL 默认写入 `/app/sessions`（已在 `.gitignore` 中排除），如需持久化可挂载该目录。

如需本地构建：

```bash
docker build -t qb-monitor .
```

### 直接运行

```bash
uv sync --no-dev
uv run python main.py
```

### 常用操作

| 操作     | 命令                                   |
| -------- | -------------------------------------- |
| 启动     | `uv run python main.py`                |
| 停止     | `Ctrl+C`（优雅退出，等待当前任务完成） |
| 查看日志 | `tail -f logs/qb_auto.log`             |
| 调试模式 | 设置 `logging.level: "DEBUG"`                |

## 项目结构

```text
qb_monitor/
├── main.py                  # 入口：配置加载、日志初始化、线程启动
├── client.py                # qBittorrent API 客户端封装（含熔断器 + _request）
├── models.py                # 数据模型（MatchRule 正则匹配规则）
├── orchestrator.py          # 编排器：轮询种子、任务分发、卡顿降级、异常恢复
├── ai_matcher.py            # AI 匹配器：DeepSeek Harness SDK 封装、提示词、JSON 计划校验
├── media_naming.py          # Jellyfin 官方命名规范纯函数（sanitize/电影/剧集路径）
├── handlers/
│   ├── base_handler.py      # 处理器基类：规则匹配、标签清理
│   ├── added_handler.py     # 添加处理器：跳过匹配规则的文件
│   ├── category_tag_handler.py # 分类标签处理器：post 链按动作×分类正则补打标签
│   ├── completed_handler.py # 完成处理器：删除无用文件和空目录
│   ├── monitor_handler.py   # 监控处理器：追踪卡顿种子，超时降级
│   └── organize_handler.py  # 整理处理器：AI 匹配计划 → Jellyfin 路径 → 硬链接/拷贝
├── Dockerfile               # Docker 构建文件
├── uv.lock                  # uv 锁定文件（自动生成）
├── template_config.yaml     # 配置文件模板
├── .gitignore               # Git 忽略规则
└── README.md                # 项目文档
```

### 核心模块说明

**Orchestrator** — 生产者角色，定时轮询 qBittorrent 中所有种子，命中任一注册触发标签（集合精确匹配）且有元数据的种子，批量打上 `processing` 标签后统一放入任务队列（不区分 added/completed，轮询阶段不移除触发标签）。同时收集活跃下载种子为监控批次，分发至 MonitorHandler。工作线程 dispatch 时按注册顺序首个命中的触发标签执行处理器，成功后由 Orchestrator 移除触发标签；失败则触发标签保留、下轮重新入队重试。成功后若该标签启用了 post 链（`enable_post_chain=True`），还会依次执行 post handlers（单点失败不影响后续）。

**AddedHandler / CompletedHandler** — 消费者角色，工作线程从队列取出单个种子，执行文件跳过或清理操作，完成后自动清除 `processing` 标签。

**MonitorHandler** — 消费者角色，工作线程从队列取出监控批次，统一追踪 `metaDL`/`stalledDL`/`downloading`/`forcedDL` 四类种子的卡顿时间。降级阈值 `max_active_downloads` 来自 qBittorrent 偏好设置，卡顿超过 `stall_timeout_hours` 时调用 API 降至队列底部。使用自有时间戳，不依赖 qBittorrent 内部计数器。

**OrganizeHandler** — 消费者角色，工作线程从队列取出带触发标签（如 `organize`）的已完成种子：收集可整理视频文件 → `DeepSeekMatcher.match()` 让 agent 识别并匹配 TMDB → 校验后的计划交给命名/落盘引擎按 Jellyfin 结构硬链接（失败回退拷贝）。多 worker 并发时 AI 轮次经锁串行（runtime 进程复用），文件落盘并行。AI 失败或未匹配 → 兜底镜像「保持原始相对路径」进 `fallback_dir`，或按 `on_match_failure: fail` 报错保留触发标签重试。

**标签状态机**：

```text
added → processing → (处理成功，移除 added；失败则保留触发标签，下轮重试)
completed → processing → (处理成功，移除 completed；失败则保留触发标签，下轮重试)
organize → processing → (整理成功，移除 organize；失败则保留触发标签，下轮重试)
monitoring: 批次任务，不操作种子标签，仅追踪与降级，不进 post 链
```

## 扩展 Handler

项目采用标签驱动的注册式架构，添加新的触发标签处理器只需两步：

1. 创建继承 `BaseHandler` 的处理器类，实现 `handle(self, task)` 方法
2. 在 `main.py` 中注册：`orchestrator.register_handler("your_tag", your_handler.handle)`；若需在处理成功后执行 post 链，传入 `enable_post_chain=True`；批量任务用 `orchestrator.register_batch_handler("name", handler.handle)` 注册，post 链用 `orchestrator.register_post_handler(handler.handle)` 注册（可传 `tags=` 限定仅在特定触发标签成功后执行）

触发标签由 Orchestrator 从注册表动态推导，新增标签无需改动轮询逻辑。

示例：

```python
# handlers/custom_handler.py
from handlers.base_handler import BaseHandler


class CustomHandler(BaseHandler):
    def handle(self, task):
        self.logger.info(f"Custom handling: {task.name}")
        self._cleanup_processing_tag(task.hash)
```

```python
# main.py 中注册
from handlers.custom_handler import CustomHandler

custom_handler = CustomHandler(client, rules)
orchestrator.register_handler("custom", custom_handler.handle, enable_post_chain=True)
```

同时在 qBittorrent 中添加对应的标签触发脚本即可。

## 贡献指南

### 代码规范

- 遵循 PEP 8 代码风格
- 使用类型注解（Python 3.14+ 语法，如 `str | None`、`list[int]`）
- 类和函数使用 docstring 说明用途
- 日志使用 `get_logger(__name__)` 获取 logger

### 提交流程

1. Fork 本仓库
2. 创建特性分支：`git checkout -b feature/your-feature`
3. 提交更改：`git commit -m "feat: add your feature"`
4. 推送分支：`git push origin feature/your-feature`
5. 创建 Pull Request

### 分支管理

| 分支        | 用途         |
| ----------- | ------------ |
| `main`      | 稳定发布版本 |
| `dev`       | 开发集成分支 |
| `feature/*` | 功能开发分支 |
| `fix/*`     | Bug 修复分支 |

### Commit 规范

使用 Conventional Commits 格式：

- `feat:` 新功能
- `fix:` Bug 修复
- `refactor:` 代码重构
- `docs:` 文档更新
- `chore:` 构建/工具变更

## 许可证

MIT License

## 联系方式

如有问题或建议，请通过 GitHub Issues 提交。
