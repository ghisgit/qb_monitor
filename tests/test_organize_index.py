import json
import threading

import pytest

from handlers.organize.index import FORMAT_VERSION, OrganizeIndex


def make_entry(fingerprint=None, **overrides):
    fingerprint = fingerprint if fingerprint is not None else ["a.mkv"]
    entry = {
        "fingerprint": list(fingerprint),
        "kind": "movie",
        "title": "Movie",
        "year": 2023,
        "tmdb_id": 1,
        "files": [{"file": f, "season": None, "episodes": [], "episode_title": None} for f in fingerprint],
        "dests": {f: f"/media/movies/{f}" for f in fingerprint},
    }
    entry.update(overrides)
    return entry


class TestOrganizeIndex:
    def test_roundtrip_and_reload(self, tmp_path):
        idx = OrganizeIndex(tmp_path / "idx.json")
        idx.put("h" * 40, make_entry())
        reloaded = OrganizeIndex(tmp_path / "idx.json")
        entry = reloaded.get("h" * 40)
        assert entry is not None
        assert entry["title"] == "Movie"
        assert entry["dests"] == {"a.mkv": "/media/movies/a.mkv"}
        assert entry["ts"]  # 自动打时间戳

    def test_missing_file_means_empty(self, tmp_path):
        assert OrganizeIndex(tmp_path / "nope.json").get("h" * 40) is None

    def test_corrupt_json_treated_as_empty_then_rewritten(self, tmp_path):
        path = tmp_path / "idx.json"
        path.write_text("{not json", encoding="utf-8")
        idx = OrganizeIndex(path)
        assert idx.get("h" * 40) is None
        idx.put("h" * 40, make_entry())  # 重写为合法索引
        assert OrganizeIndex(path).get("h" * 40) is not None

    def test_wrong_version_treated_as_empty(self, tmp_path):
        path = tmp_path / "idx.json"
        path.write_text(json.dumps({"version": FORMAT_VERSION + 1, "torrents": {}}), encoding="utf-8")
        assert OrganizeIndex(path).get("h" * 40) is None

    def test_dropped_invalid_entries_on_load(self, tmp_path):
        path = tmp_path / "idx.json"
        good = make_entry()
        bad = make_entry(fingerprint=["a.mkv", "b.mkv"], dests={"a.mkv": "/x"})  # dests 与指纹不一致
        path.write_text(
            json.dumps({"version": FORMAT_VERSION, "torrents": {"good": good, "bad": bad}}), encoding="utf-8"
        )
        reloaded = OrganizeIndex(path)
        assert reloaded.get("good") is not None
        assert reloaded.get("bad") is None

    def test_invalid_put_rejected(self, tmp_path):
        idx = OrganizeIndex(tmp_path / "idx.json")
        with pytest.raises(ValueError):
            idx.put("h" * 40, {"kind": "movie"})  # 缺字段

    def test_put_is_atomic_no_tmp_leftover(self, tmp_path):
        path = tmp_path / "idx.json"
        idx = OrganizeIndex(path)
        idx.put("h" * 40, make_entry())
        assert not (tmp_path / "idx.json.tmp").exists()

    def test_concurrent_puts_no_loss(self, tmp_path):
        path = tmp_path / "idx.json"
        idx = OrganizeIndex(path)

        def worker(i):
            idx.put(
                f"{i:040d}",
                make_entry(
                    fingerprint=[f"f{i}.mkv"],
                    title=f"M{i}",
                    year=2000 + i,
                    tmdb_id=i + 1,
                    files=[{"file": f"f{i}.mkv", "season": None, "episodes": [], "episode_title": None}],
                    dests={f"f{i}.mkv": f"/media/m/{i}.mkv"},
                ),
            )

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        reloaded = OrganizeIndex(path)
        for i in range(8):
            assert reloaded.get(f"{i:040d}") is not None

    def test_hand_edited_extra_dest_key_invalidates(self, tmp_path):
        path = tmp_path / "idx.json"
        idx = OrganizeIndex(path)
        idx.put("h" * 40, make_entry())
        data = json.loads(path.read_text(encoding="utf-8"))
        data["torrents"]["h" * 40]["dests"]["b.mkv"] = "/media/b.mkv"  # 与指纹不一致
        path.write_text(json.dumps(data), encoding="utf-8")
        assert OrganizeIndex(path).get("h" * 40) is None
