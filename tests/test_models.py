from models import MatchRule


class TestMatchRule:
    def test_compiles_pattern_on_init(self):
        rule = MatchRule(r"\.nfo$")
        assert rule.compiled is not None
        assert rule.pattern == r"\.nfo$"

    def test_matches_suffix(self):
        rule = MatchRule(r"\.nfo$")
        assert rule.matches("movie.nfo")
        assert not rule.matches("movie.nfo.bak")

    def test_case_insensitive(self):
        rule = MatchRule(r"sample")
        assert rule.matches("Sample.mkv")
        assert rule.matches("SAMPLE.mkv")
        assert rule.matches("sample.mkv")

    def test_no_match(self):
        rule = MatchRule(r"\.txt$")
        assert not rule.matches("movie.mkv")
        assert not rule.matches("")

    def test_empty_pattern_matches_everything(self):
        rule = MatchRule("")
        assert rule.matches("anything.goes")

    def test_pattern_with_digits(self):
        rule = MatchRule(r"subs?\d*")
        assert rule.matches("subs")
        assert rule.matches("sub1")
        assert rule.matches("subtitle")
