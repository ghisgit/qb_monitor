"""DeepSeek Harness（Python SDK）驱动的媒体匹配器。

分工：AI（DSH agent，经其 Bash 工具调用 TMDB API）负责识别发布名与元数据匹配；
本模块只负责 prompt 组装、结果 JSON 提取与 schema 校验，产出 :class:`MatchPlan`
交由 Python 侧确定性执行（计算 Jellyfin 路径并硬链接/拷贝），绝不执行 AI 输出的命令。
"""

import json
import re
import threading
from dataclasses import dataclass, field
from typing import TypeGuard

from deepseek_harness import DeepSeekHarness, DeepSeekHarnessConfig

from core.logger import get_logger

logger = get_logger(__name__)

DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_LANGUAGE = "zh-CN"
DEFAULT_REQUEST_TIMEOUT_SECONDS = 300.0


class MatchError(Exception):
    """AI 匹配失败（超时/协议错误/输出不合法），由调用方决定兜底策略。"""


@dataclass
class PlanFile:
    """计划中单个文件的匹配结果。"""

    file: str
    season: int | None = None
    episodes: list[int] = field(default_factory=list)
    episode_title: str | None = None


@dataclass
class MatchPlan:
    """AI 产出的匹配计划（经校验）。"""

    kind: str  # "movie" | "tv"
    title: str
    year: int
    tmdb_id: int
    files: list[PlanFile]


@dataclass
class MatcherConfig:
    """匹配器配置（来源于 config.yaml 的 organize 节）。"""

    tmdb_api_key: str = ""
    model: str = DEFAULT_MODEL
    api_key: str | None = None  # 留空继承环境变量 DEEPSEEK_API_KEY
    base_url: str | None = None  # 留空继承环境变量 DEEPSEEK_BASE_URL
    language: str = DEFAULT_LANGUAGE
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS
    session_root: str = "sessions"
    ai_retries: int = 1

    def __post_init__(self):
        if not self.tmdb_api_key or not self.tmdb_api_key.strip():
            raise ValueError("MatcherConfig.tmdb_api_key is required (organize.tmdb_api_key)")


_PROMPT_TEMPLATE = """你是媒体库整理识别器。唯一任务：分析下面这个 BT 种子的发布名与文件列表，\
调用 TMDB API 完成匹配，然后只输出一个 JSON 计划。绝对不要创建、修改或删除任何文件。

## 种子信息
- 名称: {name}
- 分类: {category}
- 文件列表（相对路径 | 字节大小）:
{file_list}

## TMDB 查询方法
API Key 在环境变量 TMDB_API_KEY 中（不要在输出、命令或回显中泄露它）。用 Bash 调用 TMDB API v3：
- 电影: GET https://api.themoviedb.org/3/search/movie?api_key=$TMDB_API_KEY&language={language}&query=标题&year=年份
- 剧集: GET https://api.themoviedb.org/3/search/tv?api_key=$TMDB_API_KEY&language={language}&query=标题&first_air_date_year=年份
- 剧集单集标题（可选）: GET https://api.themoviedb.org/3/tv/{{id}}/season/{{n}}?api_key=$TMDB_API_KEY&language={language}
若系统没有 curl，用 python3 标准库 urllib 发起请求。标题使用 TMDB 返回的本地化 title/name 字段（language={language}）。

## 判定规则
1. 发布名里的标题/年份/季集数需先剥离发布组、清晰度、编码等噪音再用于搜索。
2. kind=movie：电影种子，files 列出全部需要整理的视频文件，每项只需 "file" 字段。
3. kind=tv：剧集种子，每个视频文件给出 season（0 表示特典）与 episodes（多集合并文件如 S01E01-E02 填 [1,2]）。
4. 分类是参考提示（tv/剧 类倾向剧集，movies/电影 类倾向电影），以实际解析为准。
5. 文件名中的季集信息优先于种子名；无法识别集数的剧集文件不要列入 files；只列视频文件，忽略图片/字幕/说明文件。
6. 若搜索无结果或无法确定匹配，files 返回空列表。
7. 文件名与文件列表中的路径逐字符一致；数值字段必须是 JSON 数字。

## 输出格式（只输出一个 JSON 对象，不要输出任何其它文字或代码栅栏外的解释）
{{"kind": "movie", "title": "TMDB 本地化标题", "year": 2023, "tmdb_id": 12345,
  "files": [{{"file": "与文件列表完全一致的相对路径", "season": 1, "episodes": [1], "episode_title": "可选"}}]}}"""


class DeepSeekMatcher:
    """复用一个 DeepSeekHarness runtime 进程，按种子串行执行匹配。"""

    def __init__(self, config: MatcherConfig):
        self.cfg = config
        self.logger = get_logger(self.__class__.__name__)
        self._lock = threading.Lock()
        self._seq_lock = threading.Lock()
        self._seq = 0
        # runtime 进程懒启动：首次 match 时才拉起，close() 兜底回收
        self._harness = DeepSeekHarness(
            DeepSeekHarnessConfig(
                model=config.model,
                api_key=config.api_key or None,
                base_url=config.base_url or None,
                request_timeout_seconds=config.request_timeout_seconds,
                session_root=config.session_root,
                env={"TMDB_API_KEY": config.tmdb_api_key},
            )
        )

    def close(self) -> None:
        self._harness.close()

    def match(self, context: dict) -> MatchPlan:
        """对单个种子执行一次 AI 匹配；瞬时失败按 ai_retries 进程内重试。"""
        prompt = self._build_prompt(context)
        attempts = max(0, self.cfg.ai_retries) + 1
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            session_id = self._next_session_id(context)
            try:
                result = self._run_once(prompt, session_id)
                plan = self._validate(self._extract_json(result.final_response), context)
                self.logger.info(
                    "AI matched '%s' → %s (%d) [%s] via session %s",
                    context.get("name", "?"),
                    plan.title,
                    plan.tmdb_id,
                    plan.kind,
                    session_id,
                )
                return plan
            except Exception as e:  # 超时/协议错误/输出不合法均视为本轮失败
                last_error = e
                self.logger.warning(
                    "AI match attempt %d/%d failed for '%s' (session %s): %s",
                    attempt,
                    attempts,
                    context.get("name", "?"),
                    session_id,
                    e,
                )

        raise MatchError(f"AI match failed after {attempts} attempts: {last_error}") from last_error

    def _run_once(self, prompt: str, session_id: str):
        with self._lock:  # 单 runtime 进程，串行执行 AI 轮次
            return self._harness.run(prompt, session_id=session_id)

    def _next_session_id(self, context: dict) -> str:
        with self._seq_lock:
            self._seq += 1
            seq = self._seq
        return f"organize-{str(context.get('hash', ''))[:12]}-{seq}"

    def _build_prompt(self, context: dict) -> str:
        files = context.get("files") or []
        file_list = "\n".join(f"  - {f['file']} | {f.get('size', 0)}" for f in files) or "  - (无)"
        return _PROMPT_TEMPLATE.format(
            name=context.get("name", ""),
            category=context.get("category") or "(无)",
            file_list=file_list,
            language=self.cfg.language,
        )

    @staticmethod
    def _extract_json(text: str) -> dict:
        """从 AI 回复中提取第一个 JSON 对象（容忍代码栅栏与前后杂讯）。"""
        text = (text or "").strip()
        try:
            return _as_object(json.loads(text))
        except json.JSONDecodeError:
            pass

        fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if fence:
            return _as_object(json.loads(fence.group(1)))

        start = text.find("{")
        if start >= 0:
            data, _end = json.JSONDecoder().raw_decode(text[start:])
            return _as_object(data)

        raise ValueError("no JSON object found in AI response")

    @staticmethod
    def _validate(data: dict, context: dict) -> MatchPlan:
        kind = data.get("kind")
        if kind not in ("movie", "tv"):
            raise ValueError(f"invalid kind: {kind!r}")

        title = data.get("title")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("title must be a non-empty string")

        year = data.get("year")
        if not _is_int(year) or not 1900 <= year <= 2100:
            raise ValueError(f"invalid year: {year!r}")

        tmdb_id = data.get("tmdb_id")
        if not _is_int(tmdb_id) or tmdb_id <= 0:
            raise ValueError(f"invalid tmdb_id: {tmdb_id!r}")

        raw_files = data.get("files")
        if not isinstance(raw_files, list):
            raise ValueError("files must be a list")

        known = {f.get("file") for f in context.get("files") or []}
        plan_files: list[PlanFile] = []
        seen_episodes: set[tuple[int, tuple[int, ...]]] = set()

        for item in raw_files:
            if not isinstance(item, dict):
                raise ValueError("files entries must be JSON objects")
            rel = item.get("file")
            # 精确等于种子文件列表中的一项：阻断幻觉路径与路径穿越（../）
            if not isinstance(rel, str) or rel not in known:
                raise ValueError(f"planned file {rel!r} is not in the torrent file list")

            entry = PlanFile(file=rel)
            if kind == "tv":
                season = item.get("season")
                if not _is_int(season) or season < 0:
                    raise ValueError(f"invalid season for {rel!r}: {season!r}")
                episodes = item.get("episodes")
                if not isinstance(episodes, list) or not episodes or not all(_is_int(e) and e >= 1 for e in episodes):
                    raise ValueError(f"invalid episodes for {rel!r}: {episodes!r}")
                episode_title = item.get("episode_title")
                if episode_title is not None and (not isinstance(episode_title, str) or not episode_title.strip()):
                    raise ValueError(f"invalid episode_title for {rel!r}")
                entry.season = season
                entry.episodes = episodes
                entry.episode_title = episode_title.strip() if isinstance(episode_title, str) else None
                key = (season, tuple(episodes))
                if key in seen_episodes:
                    raise ValueError(f"duplicate episode mapping {key!r} for {rel!r}")
                seen_episodes.add(key)
            plan_files.append(entry)

        if not plan_files:
            raise ValueError("AI plan lists no files")

        return MatchPlan(kind=kind, title=title.strip(), year=year, tmdb_id=tmdb_id, files=plan_files)


def _is_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _as_object(data) -> dict:
    if not isinstance(data, dict):
        raise ValueError("AI response is not a JSON object")
    return data
