import json
import logging

import pytest

from app.core.logging import configure_logging


def test_emits_json_with_extras(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("DEBUG")
    logging.getLogger("t").info("hello", extra={"query_id": "q1", "tokens": 5})
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["msg"] == "hello"
    assert line["level"] == "INFO"
    assert line["query_id"] == "q1"
    assert line["tokens"] == 5


def test_level_filters(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("WARNING")
    logging.getLogger("t").info("quiet")
    assert capsys.readouterr().out == ""


def test_exception_included(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        logging.getLogger("t").exception("failed")
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert "RuntimeError: boom" in line["exc"]
