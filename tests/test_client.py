import pytest

from client import retry


class TestRetryDecorator:
    def test_succeeds_first_attempt(self):
        call_count = 0

        @retry(max_attempts=3, delay=0.01)
        def action():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = action()
        assert result == "ok"
        assert call_count == 1

    def test_succeeds_after_retries(self):
        call_count = 0

        @retry(max_attempts=5, delay=0.01)
        def action():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("temporary")
            return "recovered"

        result = action()
        assert result == "recovered"
        assert call_count == 3

    def test_fails_after_max_attempts(self):
        call_count = 0

        @retry(max_attempts=3, delay=0.01)
        def action():
            nonlocal call_count
            call_count += 1
            raise ValueError("persistent")

        with pytest.raises(ValueError, match="persistent"):
            action()
        assert call_count == 3

    def test_respects_exception_filter(self):
        @retry(max_attempts=2, delay=0.01, exceptions=(KeyError,))
        def action():
            raise ValueError("not caught")

        with pytest.raises(ValueError, match="not caught"):
            action()

    def test_retry_with_backoff_still_retries(self):
        call_count = 0

        @retry(max_attempts=5, delay=0.005, backoff=2.0)
        def action():
            nonlocal call_count
            call_count += 1
            if call_count < 4:
                raise RuntimeError("not yet")
            return "done"

        assert action() == "done"
        assert call_count == 4
