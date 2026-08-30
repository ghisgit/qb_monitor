import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from qbittorrentapi import TorrentDictionary

from ai_matcher import MatchError, MatchPlan, PlanFile
from handlers.organize_handler import OrganizeHandler
from organize_index import OrganizeIndex


class FakeFile:
    def __init__(self, name: str, size: int = 1024 * 1024, priority: int = 1):
        self.name = name
        self.size = size
        self.priority = priority


class FakeTorrent(TorrentDictionary):
    """真实 TorrentDictionary 子类：仅覆写 files（避免走 qB API），其余走 dict 数据。"""

    def __init__(self, name, save_path, files, category="", progress=1.0, hash_=None):
        data: dict[str, Any] = {
            "hash": hash_ or "a" * 40,
            "name": name,
            "save_path": str(save_path),
            "category": category,
            "progress": progress,
        }
        super().__init__(client=MagicMock(), data=data)
        self._fake_files = files

    @property
    def files(self):
        return self._fake_files


class StubMatcher:
    def __init__(self, plan=None, error=None):
        self.plan = plan
        self.error = error
        self.contexts = []

    def match(self, context):
        self.contexts.append(context)
        if self.error is not None:
            raise self.error
        return self.plan


@pytest.fixture
def layout(tmp_path):
    return SimpleNamespace(
        downloads=tmp_path / "downloads",
        movies=tmp_path / "movies",
        tv=tmp_path / "tv",
        fallback=tmp_path / "unmatched",
    )


def make_handler(layout, matcher, client=None, index=None, **overrides):
    # 与 main.py 传入的 organize_cfg / template_config.yaml 一致：library 为嵌套节
    cfg = {
        "library": {
            "movies_dir": str(layout.movies),
            "tv_dir": str(layout.tv),
            "fallback_dir": str(layout.fallback),
        },
        "on_exists": "skip",
        "on_match_failure": "fallback",
        "min_file_size_mb": 0,
        "include_episode_title": False,
        "include_tmdb_id": True,
    }
    cfg["library"].update(overrides.pop("library", None) or {})
    cfg.update(overrides)
    return OrganizeHandler(client or MagicMock(), matcher, cfg, index=index)


def movie_plan(file: str, title="Movie Name", year=2023, tmdb_id=555) -> MatchPlan:
    return MatchPlan(kind="movie", title=title, year=year, tmdb_id=tmdb_id, files=[PlanFile(file=file)])


def write_file(path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def movie_dest(layout, title, year, tmdb_id, name):
    return layout.movies / f"{title} ({year}) [tmdbid-{tmdb_id}]" / name


class TestMovieSingleFile:
    def test_hardlinked_into_jellyfin_path(self, layout):
        src = layout.downloads / "Movie.Name.2023.1080p.mkv"
        src.parent.mkdir(parents=True)
        src.write_bytes(b"movie-bytes")

        client = MagicMock()
        handler = make_handler(layout, StubMatcher(plan=movie_plan("Movie.Name.2023.1080p.mkv")), client=client)
        handler.handle(
            FakeTorrent(
                "Movie.Name.2023.1080p.mkv",
                layout.downloads,
                [FakeFile("Movie.Name.2023.1080p.mkv")],
                category="movies",
            )
        )

        dest = movie_dest(layout, "Movie Name", 2023, 555, "Movie Name (2023) [tmdbid-555].mkv")
        assert dest.is_file()
        assert dest.read_bytes() == b"movie-bytes"
        assert os.stat(dest).st_ino == os.stat(src).st_ino  # 真硬链接
        client.remove_torrents_tag.assert_called_once_with(hashes="a" * 40, tag="processing")

    def test_cross_device_fallback_copy(self, layout, monkeypatch):
        src = layout.downloads / "Movie.2020.mkv"
        src.parent.mkdir(parents=True)
        src.write_bytes(b"content")

        def boom(*_args, **_kwargs):
            raise OSError(18, "Invalid cross-device link")  # EXDEV

        monkeypatch.setattr("handlers.organize_handler.os.link", boom)
        handler = make_handler(layout, StubMatcher(plan=movie_plan("Movie.2020.mkv", year=2020)))
        handler.handle(FakeTorrent("Movie.2020.mkv", layout.downloads, [FakeFile("Movie.2020.mkv")]))

        dest = movie_dest(layout, "Movie Name", 2020, 555, "Movie Name (2020) [tmdbid-555].mkv")
        assert dest.is_file()
        assert dest.read_bytes() == b"content"
        assert os.stat(dest).st_ino != os.stat(src).st_ino  # 拷贝而非硬链接

    def test_multi_video_uses_cd_parts(self, layout):
        for name in ("Movie.2020.cd1.mkv", "Movie.2020.cd2.mkv"):
            (layout.downloads / name).parent.mkdir(parents=True, exist_ok=True)
            (layout.downloads / name).write_bytes(name.encode())

        plan = MatchPlan(
            kind="movie",
            title="Movie Name",
            year=2020,
            tmdb_id=7,
            files=[PlanFile(file="Movie.2020.cd1.mkv"), PlanFile(file="Movie.2020.cd2.mkv")],
        )
        handler = make_handler(layout, StubMatcher(plan=plan))
        handler.handle(
            FakeTorrent(
                "Movie.2020", layout.downloads, [FakeFile("Movie.2020.cd1.mkv"), FakeFile("Movie.2020.cd2.mkv")]
            )
        )

        base = layout.movies / "Movie Name (2020) [tmdbid-7]"
        assert (base / "Movie Name (2020) [tmdbid-7]-cd1.mkv").is_file()
        assert (base / "Movie Name (2020) [tmdbid-7]-cd2.mkv").is_file()


class TestSharedRootFolder:
    def test_torrents_share_root_only_own_files_organized(self, layout):
        """问题 2 回归：种子 A/B 根目录同名 'ab'，下载目录合并。

        每个种子的 task.files 只含自己条目；整理必须逐文件（绝不使用共享的 content_path）。
        """
        ab = layout.downloads / "ab"
        ab.mkdir(parents=True)
        (ab / "A.mp4").write_bytes(b"A")
        (ab / "B.mp4").write_bytes(b"B")

        handler_a = make_handler(
            layout, StubMatcher(plan=movie_plan("ab/A.mp4", title="Movie A", year=2020, tmdb_id=1))
        )
        handler_a.handle(FakeTorrent("Movie A", layout.downloads, [FakeFile("ab/A.mp4")]))

        dest_a = movie_dest(layout, "Movie A", 2020, 1, "Movie A (2020) [tmdbid-1].mp4")
        assert dest_a.is_file()
        assert dest_a.read_bytes() == b"A"
        # B.mp4 不受 A 整理影响：既不进电影库，也不进兜底目录
        assert not list(layout.movies.glob("**/B.mp4"))
        assert not (layout.fallback / "ab" / "B.mp4").exists()

        handler_b = make_handler(
            layout, StubMatcher(plan=movie_plan("ab/B.mp4", title="Movie B", year=2020, tmdb_id=2))
        )
        handler_b.handle(FakeTorrent("Movie B", layout.downloads, [FakeFile("ab/B.mp4")]))

        dest_b = movie_dest(layout, "Movie B", 2020, 2, "Movie B (2020) [tmdbid-2].mp4")
        assert dest_b.is_file()
        assert dest_b.read_bytes() == b"B"


class TestTvSeasonPack:
    def test_season_pack_files_split_by_episode(self, layout):
        pack = layout.downloads / "Show.S01.COMPLETE"
        pack.mkdir(parents=True)
        (pack / "S01E01.mkv").write_bytes(b"e1")
        (pack / "S01E02.mkv").write_bytes(b"e2")

        plan = MatchPlan(
            kind="tv",
            title="Show",
            year=2021,
            tmdb_id=9,
            files=[
                PlanFile(file="Show.S01.COMPLETE/S01E01.mkv", season=1, episodes=[1]),
                PlanFile(file="Show.S01.COMPLETE/S01E02.mkv", season=1, episodes=[2]),
            ],
        )
        handler = make_handler(layout, StubMatcher(plan=plan))
        handler.handle(
            FakeTorrent(
                "Show.S01.COMPLETE",
                layout.downloads,
                [FakeFile("Show.S01.COMPLETE/S01E01.mkv"), FakeFile("Show.S01.COMPLETE/S01E02.mkv")],
                category="tv",
            )
        )

        season_dir = layout.tv / "Show (2021) [tmdbid-9]" / "Season 01"
        assert (season_dir / "Show (2021) [tmdbid-9] - S01E01.mkv").is_file()
        assert (season_dir / "Show (2021) [tmdbid-9] - S01E02.mkv").is_file()

    def test_multi_episode_file_naming(self, layout):
        (layout.downloads / "Show.S01E01E02.mkv").parent.mkdir(parents=True, exist_ok=True)
        (layout.downloads / "Show.S01E01E02.mkv").write_bytes(b"both")

        plan = MatchPlan(
            kind="tv",
            title="Show",
            year=2021,
            tmdb_id=9,
            files=[PlanFile(file="Show.S01E01E02.mkv", season=1, episodes=[1, 2])],
        )
        handler = make_handler(layout, StubMatcher(plan=plan))
        handler.handle(FakeTorrent("Show.S01E01E02", layout.downloads, [FakeFile("Show.S01E01E02.mkv")]))

        dest = layout.tv / "Show (2021) [tmdbid-9]" / "Season 01" / "Show (2021) [tmdbid-9] - S01E01-E02.mkv"
        assert dest.is_file()

    def test_specials_go_to_season_00(self, layout):
        write_file(layout.downloads / "Show.S00.mkv", b"sp")
        plan = MatchPlan(
            kind="tv",
            title="Show",
            year=2021,
            tmdb_id=9,
            files=[PlanFile(file="Show.S00.mkv", season=0, episodes=[1])],
        )
        handler = make_handler(layout, StubMatcher(plan=plan))
        handler.handle(FakeTorrent("Show.S00", layout.downloads, [FakeFile("Show.S00.mkv")]))
        assert (layout.tv / "Show (2021) [tmdbid-9]" / "Season 00" / "Show (2021) [tmdbid-9] - S00E01.mkv").is_file()

    def test_episode_title_appended_when_enabled(self, layout):
        write_file(layout.downloads / "Show.S01E01.mkv", b"e1")
        plan = MatchPlan(
            kind="tv",
            title="Show",
            year=2021,
            tmdb_id=9,
            files=[PlanFile(file="Show.S01E01.mkv", season=1, episodes=[1], episode_title="The Beginning")],
        )
        handler = make_handler(layout, StubMatcher(plan=plan), include_episode_title=True)
        handler.handle(FakeTorrent("Show.S01E01", layout.downloads, [FakeFile("Show.S01E01.mkv")]))
        dest = (
            layout.tv / "Show (2021) [tmdbid-9]" / "Season 01" / "Show (2021) [tmdbid-9] - S01E01 - The Beginning.mkv"
        )
        assert dest.is_file()


class TestExistingDestinations:
    def test_on_exists_skip_keeps_existing(self, layout):
        src = layout.downloads / "Movie.2023.mkv"
        src.parent.mkdir(parents=True)
        src.write_bytes(b"new")
        dest = movie_dest(layout, "Movie Name", 2023, 555, "Movie Name (2023) [tmdbid-555].mkv")
        dest.parent.mkdir(parents=True)
        dest.write_bytes(b"old")

        handler = make_handler(layout, StubMatcher(plan=movie_plan("Movie.2023.mkv")))
        handler.handle(FakeTorrent("Movie.2023", layout.downloads, [FakeFile("Movie.2023.mkv")]))

        assert dest.read_bytes() == b"old"  # 未被覆盖

    def test_on_exists_overwrite_replaces(self, layout):
        src = layout.downloads / "Movie.2023.mkv"
        src.parent.mkdir(parents=True)
        src.write_bytes(b"new")
        dest = movie_dest(layout, "Movie Name", 2023, 555, "Movie Name (2023) [tmdbid-555].mkv")
        dest.parent.mkdir(parents=True)
        dest.write_bytes(b"old")

        handler = make_handler(layout, StubMatcher(plan=movie_plan("Movie.2023.mkv")), on_exists="overwrite")
        handler.handle(FakeTorrent("Movie.2023", layout.downloads, [FakeFile("Movie.2023.mkv")]))

        assert dest.read_bytes() == b"new"

    def test_rerun_is_idempotent(self, layout):
        src = layout.downloads / "Movie.2023.mkv"
        src.parent.mkdir(parents=True)
        src.write_bytes(b"content")

        handler = make_handler(layout, StubMatcher(plan=movie_plan("Movie.2023.mkv")))
        task = FakeTorrent("Movie.2023", layout.downloads, [FakeFile("Movie.2023.mkv")])
        handler.handle(task)
        handler.handle(task)  # 第二次：同 inode 目标 → 幂等跳过

        base = movie_dest(layout, "Movie Name", 2023, 555, "Movie Name (2023) [tmdbid-555].mkv")
        assert base.is_file()
        assert len(list(base.parent.iterdir())) == 1


class TestMatchFailure:
    def test_fallback_mirrors_into_fallback_dir(self, layout):
        src = layout.downloads / "ab" / "Unknown.2020.mkv"
        src.parent.mkdir(parents=True)
        src.write_bytes(b"mystery")

        handler = make_handler(layout, StubMatcher(error=MatchError("AI down")))
        handler.handle(FakeTorrent("Unknown.2020", layout.downloads, [FakeFile("ab/Unknown.2020.mkv")]))

        dest = layout.fallback / "ab" / "Unknown.2020.mkv"
        assert dest.is_file()
        assert os.stat(dest).st_ino == os.stat(src).st_ino

    def test_fail_mode_raises(self, layout):
        src = layout.downloads / "Unknown.2020.mkv"
        src.parent.mkdir(parents=True)
        src.write_bytes(b"x")

        handler = make_handler(layout, StubMatcher(error=MatchError("AI down")), on_match_failure="fail")
        with pytest.raises(MatchError):
            handler.handle(FakeTorrent("Unknown.2020", layout.downloads, [FakeFile("Unknown.2020.mkv")]))


class TestFileSelection:
    def test_non_video_files_excluded_from_context(self, layout):
        write_file(layout.downloads / "Movie.2023.mkv", b"v")
        write_file(layout.downloads / "movie.nfo", b"nfo" * 5000)

        matcher = StubMatcher(plan=movie_plan("Movie.2023.mkv"))
        handler = make_handler(layout, matcher)
        handler.handle(FakeTorrent("Movie.2023", layout.downloads, [FakeFile("Movie.2023.mkv"), FakeFile("movie.nfo")]))

        assert [f["file"] for f in matcher.contexts[0]["files"]] == ["Movie.2023.mkv"]
        assert (layout.movies / "Movie Name (2023) [tmdbid-555]").is_dir()

    def test_min_file_size_filter(self, layout):
        write_file(layout.downloads / "Big.mkv", b"big")
        write_file(layout.downloads / "sample.mkv", b"tiny")

        matcher = StubMatcher(plan=movie_plan("Big.mkv"))
        handler = make_handler(layout, matcher, min_file_size_mb=1)
        handler.handle(FakeTorrent("Big", layout.downloads, [FakeFile("Big.mkv"), FakeFile("sample.mkv", size=1024)]))

        assert [f["file"] for f in matcher.contexts[0]["files"]] == ["Big.mkv"]
        assert (layout.movies / "Movie Name (2023) [tmdbid-555]").is_dir()

    def test_missing_file_on_disk_excluded(self, layout):
        write_file(layout.downloads / "A.mkv", b"a")
        matcher = StubMatcher(plan=movie_plan("A.mkv"))
        handler = make_handler(layout, matcher)
        handler.handle(FakeTorrent("A", layout.downloads, [FakeFile("A.mkv"), FakeFile("Ghost.mkv")]))

        assert [f["file"] for f in matcher.contexts[0]["files"]] == ["A.mkv"]
        dest = movie_dest(layout, "Movie Name", 2023, 555, "Movie Name (2023) [tmdbid-555].mkv")
        assert dest.is_file()  # Ghost.mkv 不存在于磁盘，不影响 A 的整理

    def test_no_eligible_files_is_success(self, layout):
        write_file(layout.downloads / "movie.nfo", b"nfo")
        matcher = StubMatcher(plan=movie_plan("ghost.mkv"))
        handler = make_handler(layout, matcher)
        handler.handle(FakeTorrent("NfoOnly", layout.downloads, [FakeFile("movie.nfo")]))  # 不应抛错
        assert matcher.contexts == []


class TestGuards:
    def test_incomplete_torrent_raises(self, layout):
        client = MagicMock()
        handler = make_handler(layout, StubMatcher(plan=movie_plan("x.mkv")), client=client)
        task = FakeTorrent("X", layout.downloads, [FakeFile("x.mkv")], progress=0.5)
        with pytest.raises(RuntimeError, match="not complete"):
            handler.handle(task)
        client.remove_torrents_tag.assert_called_once_with(hashes=task.hash, tag="processing")

    def test_processing_tag_removed_even_on_failure(self, layout):
        write_file(layout.downloads / "x.mkv", b"x")
        client = MagicMock()
        handler = make_handler(layout, StubMatcher(error=MatchError("boom")), client=client, on_match_failure="fail")
        task = FakeTorrent("X", layout.downloads, [FakeFile("x.mkv")])
        with pytest.raises(MatchError):
            handler.handle(task)
        client.remove_torrents_tag.assert_called_once_with(hashes=task.hash, tag="processing")


class TestCachedPlan:
    """已整理种子再次整理：索引命中 → 跳过 AI 全流程；指纹变化/无索引 → 正常重跑。"""

    def test_rerun_skips_ai_when_unchanged(self, layout, tmp_path):
        write_file(layout.downloads / "Movie.2023.mkv", b"content")
        matcher = StubMatcher(plan=movie_plan("Movie.2023.mkv"))
        handler = make_handler(layout, matcher, index=OrganizeIndex(tmp_path / "idx.json"))
        task = FakeTorrent("Movie.2023", layout.downloads, [FakeFile("Movie.2023.mkv")])
        handler.handle(task)
        handler.handle(task)

        assert len(matcher.contexts) == 1  # 第二次未再调用 AI
        assert movie_dest(layout, "Movie Name", 2023, 555, "Movie Name (2023) [tmdbid-555].mkv").is_file()

    def test_rerun_relinks_missing_dest_without_ai(self, layout, tmp_path):
        src = layout.downloads / "Movie.2023.mkv"
        write_file(src, b"content")
        matcher = StubMatcher(plan=movie_plan("Movie.2023.mkv"))
        handler = make_handler(layout, matcher, index=OrganizeIndex(tmp_path / "idx.json"))
        task = FakeTorrent("Movie.2023", layout.downloads, [FakeFile("Movie.2023.mkv")])
        handler.handle(task)

        dest = movie_dest(layout, "Movie Name", 2023, 555, "Movie Name (2023) [tmdbid-555].mkv")
        assert dest.is_file()
        dest.unlink()  # 用户删除了媒体库中的文件

        handler.handle(task)
        assert len(matcher.contexts) == 1  # 仍不打 AI
        assert dest.is_file()  # 按记录的计划与路径补回
        assert os.stat(dest).st_ino == os.stat(src).st_ino

    def test_new_file_in_torrent_forces_ai(self, layout, tmp_path):
        write_file(layout.downloads / "Movie.2023.mkv", b"a")
        matcher = StubMatcher(plan=movie_plan("Movie.2023.mkv"))
        handler = make_handler(layout, matcher, index=OrganizeIndex(tmp_path / "idx.json"))
        handler.handle(FakeTorrent("Movie.2023", layout.downloads, [FakeFile("Movie.2023.mkv")]))

        write_file(layout.downloads / "Movie.2023.part2.mkv", b"b")
        handler.handle(
            FakeTorrent("Movie.2023", layout.downloads, [FakeFile("Movie.2023.mkv"), FakeFile("Movie.2023.part2.mkv")])
        )

        assert len(matcher.contexts) == 2  # 指纹变化 → 重跑 AI
        assert [f["file"] for f in matcher.contexts[1]["files"]] == ["Movie.2023.mkv", "Movie.2023.part2.mkv"]

    def test_copy_fallback_files_also_skip(self, layout, tmp_path, monkeypatch):
        src = layout.downloads / "Movie.2023.mkv"
        write_file(src, b"content")
        matcher = StubMatcher(plan=movie_plan("Movie.2023.mkv"))
        handler = make_handler(layout, matcher, index=OrganizeIndex(tmp_path / "idx.json"))
        task = FakeTorrent("Movie.2023", layout.downloads, [FakeFile("Movie.2023.mkv")])

        def boom(*_args, **_kwargs):
            raise OSError(18, "Invalid cross-device link")  # EXDEV → 拷贝落盘

        monkeypatch.setattr("handlers.organize_handler.os.link", boom)
        handler.handle(task)
        dest = movie_dest(layout, "Movie Name", 2023, 555, "Movie Name (2023) [tmdbid-555].mkv")
        assert dest.is_file()
        assert os.stat(dest).st_ino != os.stat(src).st_ino  # 拷贝而非硬链接

        handler.handle(task)  # 拷贝落盘同样命中索引 → 跳过 AI
        assert len(matcher.contexts) == 1

    def test_config_change_uses_recorded_dests(self, layout, tmp_path):
        write_file(layout.downloads / "Movie.2023.mkv", b"content")
        matcher = StubMatcher(plan=movie_plan("Movie.2023.mkv"))
        idx = OrganizeIndex(tmp_path / "idx.json")
        task = FakeTorrent("Movie.2023", layout.downloads, [FakeFile("Movie.2023.mkv")])
        make_handler(layout, matcher, index=idx).handle(task)

        old_dest = movie_dest(layout, "Movie Name", 2023, 555, "Movie Name (2023) [tmdbid-555].mkv")
        assert old_dest.is_file()

        alt = layout.movies / "alt"
        make_handler(layout, matcher, index=idx, library={"movies_dir": str(alt)}).handle(task)

        assert len(matcher.contexts) == 1  # 缓存命中，未重跑 AI
        assert old_dest.is_file()
        assert list(alt.glob("**/*")) == []  # 新目录不产生重复落盘

    def test_corrupt_index_falls_back_to_full_flow(self, layout, tmp_path):
        path = tmp_path / "idx.json"
        path.write_text("{broken", encoding="utf-8")
        write_file(layout.downloads / "Movie.2023.mkv", b"content")
        matcher = StubMatcher(plan=movie_plan("Movie.2023.mkv"))
        handler = make_handler(layout, matcher, index=OrganizeIndex(path))
        task = FakeTorrent("Movie.2023", layout.downloads, [FakeFile("Movie.2023.mkv")])
        handler.handle(task)
        assert len(matcher.contexts) == 1
        assert OrganizeIndex(path).get(task.hash) is not None  # 索引被重写为合法

    def test_fallback_mirror_not_cached(self, layout, tmp_path):
        write_file(layout.downloads / "Movie.2023.mkv", b"content")
        idx = OrganizeIndex(tmp_path / "idx.json")
        task = FakeTorrent("Movie.2023", layout.downloads, [FakeFile("Movie.2023.mkv")])

        failing = StubMatcher(error=MatchError("AI down"))
        make_handler(layout, failing, index=idx).handle(task)
        assert (layout.fallback / "Movie.2023.mkv").is_file()

        working = StubMatcher(plan=movie_plan("Movie.2023.mkv"))
        make_handler(layout, working, index=idx).handle(task)
        assert len(working.contexts) == 1  # fallback 不缓存 → 重打标签重试 AI

    def test_index_none_never_skips(self, layout):
        write_file(layout.downloads / "Movie.2023.mkv", b"content")
        matcher = StubMatcher(plan=movie_plan("Movie.2023.mkv"))
        handler = make_handler(layout, matcher)
        task = FakeTorrent("Movie.2023", layout.downloads, [FakeFile("Movie.2023.mkv")])
        handler.handle(task)
        handler.handle(task)
        assert len(matcher.contexts) == 2  # 无索引 → 每次全流程（原行为）

    def test_index_persists_across_handler_instances(self, layout, tmp_path):
        write_file(layout.downloads / "Movie.2023.mkv", b"content")
        task = FakeTorrent("Movie.2023", layout.downloads, [FakeFile("Movie.2023.mkv")])
        path = tmp_path / "idx.json"
        make_handler(layout, StubMatcher(plan=movie_plan("Movie.2023.mkv")), index=OrganizeIndex(path)).handle(task)
        second = StubMatcher(plan=movie_plan("Movie.2023.mkv"))
        make_handler(layout, second, index=OrganizeIndex(path)).handle(task)
        assert second.contexts == []  # 新实例同索引 → 仍跳过
