from pathlib import Path

import pytest

from media_naming import (
    base_name,
    episode_code,
    episode_destination,
    movie_destination,
    sanitize_name,
)


class TestSanitizeName:
    def test_illegal_chars_replaced(self):
        assert sanitize_name('A/B:C*D?E"F<G>H|I') == "A B C D E F G H I"

    def test_whitespace_collapsed(self):
        assert sanitize_name("  A   B\t\tC  ") == "A B C"

    def test_trailing_dots_removed(self):
        assert sanitize_name("Title...") == "Title"

    def test_control_chars_removed(self):
        assert sanitize_name("A\x00B\x1fC") == "A B C"

    def test_clean_name_unchanged(self):
        assert sanitize_name("海贼王 (1999)") == "海贼王 (1999)"


class TestBaseName:
    def test_with_tmdb_id(self):
        assert base_name("Movie Name", 2023, 555) == "Movie Name (2023) [tmdbid-555]"

    def test_without_tmdb_id(self):
        assert base_name("Movie Name", 2023, 555, include_tmdb_id=False) == "Movie Name (2023)"

    def test_zero_id_omits_suffix(self):
        assert base_name("Movie Name", 2023, 0) == "Movie Name (2023)"


class TestEpisodeCode:
    def test_single(self):
        assert episode_code(1, [3]) == "S01E03"

    def test_multi(self):
        assert episode_code(1, [1, 2]) == "S01E01-E02"

    def test_season_zero(self):
        assert episode_code(0, [1]) == "S00E01"

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            episode_code(1, [])


class TestMovieDestination:
    def test_single_file(self):
        folder, file = movie_destination(Path("/m"), "T", 2020, 1)
        assert folder == Path("/m/T (2020) [tmdbid-1]")
        assert file == Path("/m/T (2020) [tmdbid-1]/T (2020) [tmdbid-1]")

    def test_multi_part(self):
        _folder, file = movie_destination(Path("/m"), "T", 2020, 2, part=1)
        assert file.name == "T (2020) [tmdbid-2]-cd1"

    def test_no_id_switch(self):
        _folder, file = movie_destination(Path("/m"), "T", 2020, 9, include_tmdb_id=False, part=1)
        assert file.name == "T (2020)-cd1"


class TestEpisodeDestination:
    def test_standard(self):
        folder, file = episode_destination(Path("/t"), "Show", 2021, 9, 1, [3])
        assert folder == Path("/t/Show (2021) [tmdbid-9]/Season 01")
        assert file == Path("/t/Show (2021) [tmdbid-9]/Season 01/Show (2021) [tmdbid-9] - S01E03")

    def test_multi_episode(self):
        _folder, file = episode_destination(Path("/t"), "Show", 2021, 9, 2, [1, 2])
        assert file.name == "Show (2021) [tmdbid-9] - S02E01-E02"

    def test_specials(self):
        _folder, file = episode_destination(Path("/t"), "Show", 2021, 9, 0, [5])
        assert file.parent.name == "Season 00"

    def test_episode_title_opt_in(self):
        _folder, file = episode_destination(
            Path("/t"), "Show", 2021, 9, 1, [1], "The Beginning", include_episode_title=True
        )
        assert file.name == "Show (2021) [tmdbid-9] - S01E01 - The Beginning"

    def test_episode_title_ignored_by_default(self):
        _folder, file = episode_destination(Path("/t"), "Show", 2021, 9, 1, [1], "The Beginning")
        assert file.name == "Show (2021) [tmdbid-9] - S01E01"
