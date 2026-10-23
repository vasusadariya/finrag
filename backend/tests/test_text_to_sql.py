import pytest

from app.llm.types import LLMResponse, Message, Usage
from app.tools.sql_exec import ExecResult, SqlExecutionError, SqlTimeoutError
from app.tools.sql_guard import GuardResult, SqlGuardUnavailableError
from app.tools.sql_policy import load_policy
from app.tools.text_to_sql import TextToSql, extract_sql


class ScriptedLLM:
    def __init__(self, *replies: str) -> None:
        self.replies, self.calls = list(replies), []  # type: ignore[var-annotated]

    def generate(self, messages: list[Message], **kw: object) -> LLMResponse:
        self.calls.append((list(messages), kw))
        text = self.replies.pop(0)
        return LLMResponse(text, [], Usage(100, 20, 5), "m", 0.0, message=Message("model", text))


class FakeGuard:
    """Rejects anything containing DROP; adds a LIMIT otherwise."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    def validate(self, sql: str) -> GuardResult:
        self.seen.append(sql)
        if "DROP" in sql.upper():
            return GuardResult(False, None, ("not_select: only SELECT statements are allowed",))
        return GuardResult(True, sql + " LIMIT 50", ())


class FakeExecutor:
    def __init__(self, *outcomes: ExecResult | Exception) -> None:
        self.outcomes, self.ran = list(outcomes), []  # type: ignore[var-annotated]

    def execute(self, sql: str) -> ExecResult:
        self.ran.append(sql)
        o = self.outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return o


RESULT = ExecResult(["v"], [[1]], 1, False, "x", 3)


def make(
    llm: ScriptedLLM, executor: FakeExecutor, guard: FakeGuard | None = None, retries: int = 2
) -> TextToSql:
    return TextToSql(llm, guard or FakeGuard(), executor, load_policy(), max_retries=retries)


def test_first_attempt_success_runs_the_guard_rewritten_sql() -> None:
    llm, ex, g = ScriptedLLM("```sql\nSELECT 1;\n```"), FakeExecutor(RESULT), FakeGuard()
    a = make(llm, ex, g).run("q")
    assert a.ok and a.sql == "SELECT 1 LIMIT 50" and a.result is RESULT and a.error is None
    assert g.seen == ["SELECT 1"] and ex.ran == ["SELECT 1 LIMIT 50"]
    assert [x.verdict for x in a.attempts] == ["ok"]
    assert a.usage.total == 125


def test_prompt_contains_schema_glossary_and_rules() -> None:
    llm = ScriptedLLM("SELECT 1")
    make(llm, FakeExecutor(RESULT)).run("q")
    system = llm.calls[0][1]["system"]
    for needle in ["xbrl_facts", "NetIncomeLoss", "unit = 'USD'", "CANNOT_ANSWER", "max(value)"]:
        assert needle in str(system), needle
    assert llm.calls[0][1]["temperature"] == 0.0


def test_guard_rejection_is_fed_back_and_retried() -> None:
    llm = ScriptedLLM("DROP TABLE companies", "SELECT 1")
    ex = FakeExecutor(RESULT)
    a = make(llm, ex).run("q")
    assert a.ok and [x.verdict for x in a.attempts] == ["rejected", "ok"]
    feedback = llm.calls[1][0][-1]
    assert feedback.role == "user" and "rejected" in feedback.text and "not_select" in feedback.text
    assert ex.ran == ["SELECT 1 LIMIT 50"]  # the rejected query never reached the executor


def test_database_error_is_fed_back_and_retried() -> None:
    llm = ScriptedLLM("SELECT nope", "SELECT 1")
    a = make(llm, FakeExecutor(SqlExecutionError('column "nope" does not exist'), RESULT)).run("q")
    assert a.ok and [x.verdict for x in a.attempts] == ["error", "ok"]
    assert 'column "nope" does not exist' in llm.calls[1][0][-1].text


def test_gives_up_after_max_retries_with_last_error() -> None:
    llm = ScriptedLLM("DROP a", "DROP b", "DROP c")
    ex = FakeExecutor()
    a = make(llm, ex).run("q")
    assert not a.ok and a.result is None and len(a.attempts) == 3 and "safety check" in str(a.error)
    assert ex.ran == [] and len(llm.calls) == 3
    one = make(ScriptedLLM("DROP a", "SELECT 1"), FakeExecutor(RESULT), retries=0).run("q")
    assert not one.ok and len(one.attempts) == 1


def test_timeouts_are_not_retried() -> None:
    llm = ScriptedLLM("SELECT 1", "SELECT 2")
    a = make(llm, FakeExecutor(SqlTimeoutError("query exceeded the 5000 ms time limit"))).run("q")
    assert not a.ok and a.attempts[-1].verdict == "timeout" and len(llm.calls) == 1


def test_cannot_answer_is_reported_without_touching_guard_or_db() -> None:
    llm, g, ex = ScriptedLLM("CANNOT_ANSWER: asks about Tesla"), FakeGuard(), FakeExecutor()
    a = make(llm, ex, g).run("Tesla revenue?")
    assert not a.ok and "Tesla" in str(a.error) and g.seen == [] and a.attempts == []


def test_guard_outage_propagates_and_nothing_runs() -> None:
    class Down:
        def validate(self, sql: str) -> GuardResult:
            raise SqlGuardUnavailableError("down")

    ex = FakeExecutor()
    with pytest.raises(SqlGuardUnavailableError):
        TextToSql(ScriptedLLM("SELECT 1"), Down(), ex, load_policy()).run("q")
    assert ex.ran == []


@pytest.mark.parametrize(
    "reply, sql",
    [
        ("SELECT 1", "SELECT 1"),
        ("```sql\nSELECT a FROM t;\n```", "SELECT a FROM t"),
        ("Here you go:\n```\nSELECT 2\n```\nHope it helps", "SELECT 2"),
        ("  SELECT 3 ;  ", "SELECT 3"),
        (
            "```SQL\nWITH x AS (SELECT 1) SELECT * FROM x\n```",
            "WITH x AS (SELECT 1) SELECT * FROM x",
        ),
    ],
)
def test_extract_sql(reply: str, sql: str) -> None:
    assert extract_sql(reply) == sql
