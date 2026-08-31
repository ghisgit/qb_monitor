import json
import logging
from io import StringIO

from core.logger import (
    ContextFilter,
    QBLogger,
    SensitiveDataFilter,
    StructuredFormatter,
)


class TestQBLogger:
    def test_fatal_logs_at_custom_level(self):
        logger = QBLogger("test_fatal")
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)

        logger.fatal("something terrible")
        output = stream.getvalue()
        assert "FATAL" in output
        assert "something terrible" in output


class TestStructuredFormatter:
    def test_text_format_produces_readable_output(self):
        formatter = StructuredFormatter(environment="development", json_format=False)
        record = logging.LogRecord(
            name="test_mod",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        assert "hello" in output
        assert "INFO" in output

    def test_json_format_contains_expected_keys(self):
        formatter = StructuredFormatter(environment="production", json_format=True)
        record = logging.LogRecord(
            name="test_mod",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello world",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)
        assert data["level"] == "INFO"
        assert data["message"] == "hello world"
        assert data["module"] == "test_mod"
        assert data["environment"] == "production"


class TestContextFilter:
    def test_sets_and_clears_attrs(self):
        ContextFilter.set(operation="testing", request_id="abc123")
        record = logging.LogRecord("t", logging.DEBUG, "", 0, "msg", (), None)
        f = ContextFilter()
        assert f.filter(record)
        assert record.operation == "testing"  # type: ignore[attr-defined]
        assert record.request_id == "abc123"  # type: ignore[attr-defined]

        ContextFilter.clear()
        record2 = logging.LogRecord("t", logging.DEBUG, "", 0, "msg", (), None)
        assert f.filter(record2)
        assert not hasattr(record2, "operation")


class TestSensitiveDataFilter:
    def test_masks_password(self):
        f = SensitiveDataFilter(enabled=True)
        record = logging.LogRecord("t", logging.INFO, "", 0, "password: my_secret", (), None)
        assert f.filter(record)
        assert "my_secret" not in record.msg
        assert "***" in record.msg

    def test_masks_token(self):
        f = SensitiveDataFilter(enabled=True)
        record = logging.LogRecord("t", logging.INFO, "", 0, "token = abc123def", (), None)
        assert f.filter(record)
        assert "abc123def" not in record.msg

    def test_disabled_passes_through(self):
        f = SensitiveDataFilter(enabled=False)
        msg = "password: secret"
        record = logging.LogRecord("t", logging.INFO, "", 0, msg, (), None)
        assert f.filter(record)
        assert record.msg == msg
