import pytest
from pydantic import ValidationError

from app.core.config import Settings

REQUIRED = {
    "DATABASE_URL": "postgresql://a",
    "DATABASE_URL_RO": "postgresql://b",
    "EDGAR_USER_AGENT": "me me@example.com",
}


def _clear(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in [*REQUIRED, "GEMINI_API_KEY", "LOG_LEVEL"]:
        monkeypatch.delenv(k, raising=False)


def test_loads_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    for k, v in REQUIRED.items():
        monkeypatch.setenv(k, v)
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.database_url == "postgresql://a"
    assert s.log_level == "INFO"
    assert s.gemini_api_key is None


@pytest.mark.parametrize("missing", list(REQUIRED))
def test_missing_required_raises(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    _clear(monkeypatch)
    for k, v in REQUIRED.items():
        if k != missing:
            monkeypatch.setenv(k, v)
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None)  # type: ignore[call-arg]
    assert missing.lower() in str(exc.value)


def test_require_llm_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    for k, v in REQUIRED.items():
        monkeypatch.setenv(k, v)
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        s.require_llm_key()
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    assert Settings(_env_file=None).require_llm_key() == "k"  # type: ignore[call-arg]
