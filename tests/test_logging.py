import json
import logging

from app.logging_setup import REDACTED, JsonFormatter


def _record(msg, *args, **extra):
    rec = logging.LogRecord("app.test", logging.INFO, __file__, 1, msg, args, None)
    for k, v in extra.items():
        setattr(rec, k, v)
    return rec


def test_emits_one_json_object_with_extras():
    out = JsonFormatter().format(_record("processed %s", "doc", document_id=7, status="done"))
    obj = json.loads(out)
    assert obj["msg"] == "processed doc"
    assert obj["level"] == "info"
    assert obj["logger"] == "app.test"
    assert obj["document_id"] == 7 and obj["status"] == "done"
    assert "ts" in obj


def test_redacts_secret_values_in_message_and_extras():
    secret = "sk-ant-super-secret-123456"
    fmt = JsonFormatter([secret])
    out = fmt.format(_record(f"key={secret}", detail={"nested": [f"x{secret}y"]}))
    assert secret not in out
    obj = json.loads(out)
    assert obj["msg"] == f"key={REDACTED}"
    assert obj["detail"] == {"nested": [f"x{REDACTED}y"]}


def test_short_values_not_used_for_redaction():
    out = JsonFormatter(["abc", ""]).format(_record("abc def"))
    assert json.loads(out)["msg"] == "abc def"


def test_exception_included():
    try:
        raise ValueError("bad")
    except ValueError:
        import sys

        rec = logging.LogRecord("x", logging.ERROR, __file__, 1, "failed", (), sys.exc_info())
    obj = json.loads(JsonFormatter().format(rec))
    assert "ValueError: bad" in obj["exc"]
