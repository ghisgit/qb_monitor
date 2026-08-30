"""Jellyfin 官方命名规范的纯函数工具（无 IO，方便单测）。

参考：
- 剧集 https://jellyfin.org/docs/general/server/media/shows/
- 电影 https://jellyfin.org/docs/general/server/media/movies/
- provider-id 后缀 https://jellyfin.org/docs/general/server/metadata/identifiers/

目标路径形如：
- 电影: {movies_dir}/{T} ({Y})[ [tmdbid-{id}]]/{T} ({Y})[ [tmdbid-{id}]][-cdN].ext
- 剧集: {tv_dir}/{T} ({Y})[ [tmdbid-{id}]]/Season {SS}/{T} ({Y}) - S{SS}E{EE}[-E{EE2}][ - {集标题}].ext
"""

import re
from collections.abc import Sequence
from pathlib import Path

# 文件系统非法字符（含控制字符）
_ILLEGAL_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_WHITESPACE = re.compile(r"\s+")


def sanitize_name(text: str) -> str:
    """将文件系统非法字符替换为空格，压缩空白，去首尾空白与结尾点号（Windows 兼容）。"""
    cleaned = _ILLEGAL_CHARS.sub(" ", text)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip()
    return cleaned.rstrip(" .")


def base_name(title: str, year: int, tmdb_id: int, include_tmdb_id: bool = True) -> str:
    """「标题 (年份)[ [tmdbid-xxxx]]」公共基名（不含扩展名）。"""
    name = f"{sanitize_name(title)} ({year})"
    if include_tmdb_id and tmdb_id:
        name += f" [tmdbid-{tmdb_id}]"
    return name


def episode_code(season: int, episodes: Sequence[int]) -> str:
    """「S01E01」或 Jellyfin 多集写法「S01E01-E02」。"""
    if not episodes:
        raise ValueError("episodes must not be empty")
    prefix = f"S{season:02d}"
    if len(episodes) == 1:
        return f"{prefix}E{episodes[0]:02d}"
    return f"{prefix}E{min(episodes):02d}-E{max(episodes):02d}"


def movie_destination(
    movies_dir: Path,
    title: str,
    year: int,
    tmdb_id: int,
    include_tmdb_id: bool = True,
    part: int | None = None,
) -> tuple[Path, Path]:
    """返回 (电影目标目录, 目标文件路径不含扩展名)。

    part 非 None 且 > 1 时按 Jellyfin 多段命名追加「-cdN」；单文件种子无需分段。
    """
    base = base_name(title, year, tmdb_id, include_tmdb_id)
    folder = Path(movies_dir) / base
    filename = base if part is None else f"{base}-cd{part}"
    return folder, folder / filename


def episode_destination(
    tv_dir: Path,
    title: str,
    year: int,
    tmdb_id: int,
    season: int,
    episodes: Sequence[int],
    episode_title: str | None = None,
    include_tmdb_id: bool = True,
    include_episode_title: bool = False,
) -> tuple[Path, Path]:
    """返回 (单集目标目录 Season NN, 目标文件路径不含扩展名)。"""
    base = base_name(title, year, tmdb_id, include_tmdb_id)
    season_dir = Path(tv_dir) / base / f"Season {season:02d}"
    filename = f"{base} - {episode_code(season, episodes)}"
    if include_episode_title and episode_title:
        filename += f" - {sanitize_name(episode_title)}"
    return season_dir, season_dir / filename
