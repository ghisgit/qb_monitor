# QB Monitor

qBittorrent 自动化监控与清理工具。基于标签驱动的事件处理架构，自动跳过不需要的文件、清理已完成种子的冗余数据，并监控卡顿种子进行优先级降级。

## 功能特点

- **添加时自动跳过文件** — 种子添加后，根据正则规则自动将匹配文件设为不下载（如样本图、NFO、字幕等）
- **完成后自动清理** — 种子下载完成后，自动删除未下载的文件（priority=0）及匹配清理规则的文件和空目录
- **卡顿种子降级** — 当活跃下载数超过阈值时，自动将长时间卡在元数据获取的种子优先级降至最低
- **异常恢复** — 启动时自动恢复上次异常退出后卡在 `processing` 状态的种子，避免任务丢失
- **优雅退出** — Ctrl+C 时等待队列中任务完成后再退出
- **高可靠 API 调用** — 所有 qBittorrent API 调用均带指数退避重试机制

## 技术栈

| 组件       | 技术                                                            |
| ---------- | --------------------------------------------------------------- |
| 语言       | Python 3.12                                                     |
| API 客户端 | [qbittorrent-api](https://github.com/rmartin16/qbittorrent-api) |
| 配置解析   | PyYAML                                                          |
| 容器化     | Docker (python:3.12-slim)                                       |
| 并发模型   | threading + queue (生产者-消费者模式)                           |

## 适用场景

- PT/BT 站点批量下载，需要自动过滤附属文件
- 下载完成后自动清理不需要的样本、广告文件
- 大量种子同时下载时，需要自动管理卡顿种子
- 7×24 小时无人值守运行

## 工作流程

```
qBittorrent 添加种子
        │
        ▼
  Shell 脚本打标签 ──→ added
        │                    │
        │            Orchestrator 轮询检测
        │                    │
        │              添加 processing 标签
        │              移除 added 标签
        │                    │
        │              放入任务队列
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
        │         │                          Orchestrator 轮询检测
        │         │                                  │
        │         │                            添加 processing 标签
        │         │                            移除 completed 标签
        │         │                                  │
        │         │                            放入任务队列
        │         │                                  │
        │         │                                  ▼
        │         │                          CompletedHandler
        │         │                          删除 priority=0 文件
        │         │                          删除匹配清理规则文件
        │         │                          递归清理空目录
        │         │                                  │
        │         └──────────────────────────────────┘
        │                     │
        │              移除 processing 标签
        │                     │
        ▼                     ▼
       完成 ✅
```

## 环境要求

- qBittorrent 4.1+ (开启 Web UI)
- Docker (推荐) 或 Python 3.12+
- Bash (用于 Shell 脚本标签触发)

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
    - "(?i)\\.(nfo|txt|jpg|png|url)$"
    - "(?i)sample"
  completed:
    - "(?i)\\.(nfo|txt|jpg|png|url)$"
    - "(?i)sample"

qbittorrent:
  host: "http://127.0.0.1:8080"
  username: "admin"
  password: "adminadmin"

processor:
  poll_interval_seconds: 30
  stall_timeout_minutes: 30
  max_worker_threads: 3

logfile: "logs/qb_auto.log"
debug_mode: false
```

### 3. 配置项说明

| 配置项                            | 类型      | 说明                                                      |
| --------------------------------- | --------- | --------------------------------------------------------- |
| `rules.added`                     | list[str] | 添加时文件名匹配规则（正则表达式），匹配的文件设为不下载  |
| `rules.completed`                 | list[str] | 完成后文件/目录名匹配规则（正则表达式），匹配的项将被删除 |
| `qbittorrent.host`                | str       | qBittorrent Web UI 地址                                   |
| `qbittorrent.username`            | str       | Web UI 用户名                                             |
| `qbittorrent.password`            | str       | Web UI 密码                                               |
| `processor.poll_interval_seconds` | int       | 轮询间隔（秒）                                            |
| `processor.stall_timeout_minutes` | int       | 卡顿超时（分钟），超过此时间的 metaDL 种子将被降级        |
| `processor.max_worker_threads`    | int       | 工作线程数                                                |
| `logfile`                         | str       | 日志文件路径                                              |
| `debug_mode`                      | bool      | 是否开启调试日志                                          |

### 4. 配置 qBittorrent 自动标签

在 qBittorrent 的 **工具 → 选项 → 下载** 中设置自动执行脚本：

| 事件       | 脚本路径                       | 参数 |
| ---------- | ------------------------------ | ---- |
| 种子添加时 | `scripts/added_torrent.sh`     | `%K` |
| 种子完成时 | `scripts/completed_torrent.sh` | `%K` |

> `%K` 代表种子哈希值，脚本会自动为种子打上 `added` 或 `completed` 标签。

如果 Shell 脚本中的 qBittorrent 地址与默认 `http://127.0.0.1:8080` 不同，需修改脚本中的 `URL` 变量。

## 运行

### Docker 运行（推荐）

```bash
docker build -t qb-monitor .

docker run -d \
  --name qb-monitor \
  --restart unless-stopped \
  -v /path/to/config.yaml:/app/config.yaml \
  -v /path/to/logs:/app/logs \
  qb-monitor
```

### 直接运行

```bash
pip install -r requirements.txt
python main.py
```

### 常用操作

| 操作     | 命令                                   |
| -------- | -------------------------------------- |
| 启动     | `python main.py`                       |
| 停止     | `Ctrl+C`（优雅退出，等待当前任务完成） |
| 查看日志 | `tail -f logs/qb_auto.log`             |
| 调试模式 | 设置 `debug_mode: true`                |

## 项目结构

```
qb_monitor/
├── main.py                  # 入口：配置加载、日志初始化、线程启动
├── client.py                # qBittorrent API 客户端封装（含重试装饰器）
├── models.py                # 数据模型（MatchRule 正则匹配规则）
├── orchestrator.py          # 编排器：轮询种子、任务分发、卡顿降级、异常恢复
├── handlers/
│   ├── base_handler.py      # 处理器基类：规则匹配、标签清理
│   ├── added_handler.py     # 添加处理器：跳过匹配规则的文件
│   └── completed_handler.py # 完成处理器：删除无用文件和空目录
├── scripts/
│   ├── added_torrent.sh     # qBittorrent 添加种子时执行的标签脚本
│   └── completed_torrent.sh # qBittorrent 完成种子时执行的标签脚本
├── Dockerfile               # Docker 构建文件
├── requirements.txt         # Python 依赖
├── template_config.yaml     # 配置文件模板
├── .gitignore               # Git 忽略规则
└── README.md                # 项目文档
```

### 核心模块说明

**Orchestrator** — 生产者角色，定时轮询 qBittorrent 中带有 `added`/`completed` 标签的种子，将其放入任务队列。同时监控卡顿种子，在活跃下载数超过 200 时将超时的 metaDL 种子降级。

**Handler** — 消费者角色，工作线程从队列中取出任务并分发到对应处理器。每个处理器处理完毕后自动清理 `processing` 标签。

**标签状态机**：

```
added → processing → (处理完成，标签清除)
completed → processing → (处理完成，标签清除)
```

## 扩展 Handler

项目采用注册式架构，添加新的处理器只需两步：

1. 创建继承 `BaseHandler` 的处理器类，实现 `handle(self, task)` 方法
2. 在 `main.py` 中注册：`orchestrator.register_handler("your_tag", your_handler.handle)`

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
orchestrator.register_handler("custom", custom_handler.handle)
```

同时在 qBittorrent 中添加对应的标签触发脚本即可。

## 贡献指南

### 代码规范

- 遵循 PEP 8 代码风格
- 使用类型注解（Python 3.12+ 语法，如 `str | None`、`list[int]`）
- 类和函数使用 docstring 说明用途
- 日志使用 `logging.getLogger(__name__)` 获取 logger

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
