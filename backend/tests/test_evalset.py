import json
from pathlib import Path

import psycopg
import pytest
from pydantic import ValidationError

from app.evalkit.dataset import (
    GoldSource,
    Question,
    load_questions,
    validate_against_db,
    validate_set,
)
from app.retrieval.types import Hit


def src(**kw: object) -> dict[str, object]:
    return {
        "ticker": "AAPL",
        "fiscal_year": 2025,
        "section": "Item 2",
        "contains": "Cupertino",
        **kw,
    }


def q(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "q001",
        "question": "Where is Apple headquartered?",
        "type": "factual",
        "tickers": ["AAPL"],
        "gold_sources": [src()],
        "gold_answer": "Cupertino, California.",
    }
    base.update(kw)
    return base


def test_valid_factual_question() -> None:
    m = Question.model_validate(q())
    assert m.answerable and m.filters().tickers == ("AAPL",)


@pytest.mark.parametrize(
    "bad",
    [
        {"id": "x1"},  # id format
        {"gold_answer": None},  # answerable needs an answer
        {"gold_sources": []},  # ... and evidence
        {"unanswerable_reason": "future"},  # answerable must not carry a reason
        {"tickers": ["TSLA"]},
        {"gold_sources": [src(section="Item 99")]},
        {"gold_sources": [src(ticker="TSLA")]},
        {"gold_sources": [src(contains="ab")]},
        {"type": "multi_hop"},  # one piece of evidence only
        {"type": "cross_year"},  # one fiscal year only
        {"type": "trend"},
    ],
)
def test_invalid_questions_rejected(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Question.model_validate(q(**bad))


def test_unanswerable_rules() -> None:
    ok: dict[str, object] = {
        "type": "unanswerable",
        "gold_sources": [],
        "gold_answer": None,
        "unanswerable_reason": "advice",
    }
    assert not Question.model_validate(q(**ok)).answerable
    for extra in ({"gold_answer": "x"}, {"gold_sources": [src()]}, {"unanswerable_reason": None}):
        with pytest.raises(ValidationError):
            Question.model_validate(q(**{**ok, **extra}))


def test_multi_hop_and_cross_year_accept_enough_evidence() -> None:
    Question.model_validate(
        q(type="multi_hop", gold_sources=[src(), src(ticker="MSFT", contains="Redmond")])
    )
    Question.model_validate(q(type="cross_year", gold_sources=[src(), src(fiscal_year=2024)]))
    Question.model_validate(
        q(
            type="trend",
            gold_sources=[],
            xbrl_refs=[
                {"ticker": "AAPL", "concept": "Assets", "fiscal_year": y, "value": 1.0}
                for y in (2024, 2025)
            ],
        )
    )


def test_gold_source_matching() -> None:
    gs = GoldSource.model_validate(src())
    hit = Hit(1, 0.0, "HQ is in CUPERTINO, CA", "AAPL", "Apple", 2025, "Item 2", 19, "acc")
    assert gs.matches(hit)
    assert not gs.matches(
        Hit(1, 0.0, "HQ is in CUPERTINO", "AAPL", "Apple", 2024, "Item 2", 1, "acc")
    )
    assert not gs.matches(Hit(1, 0.0, "elsewhere", "AAPL", "Apple", 2025, "Item 2", 1, "acc"))
    assert not gs.matches(Hit(1, 0.0, "Cupertino", "MSFT", "Microsoft", 2025, "Item 2", 1, "acc"))


def _questions(n: int, **override: object) -> list[Question]:
    out = []
    for i in range(n):
        out.append(
            Question.model_validate(
                q(id=f"q{i + 1:03d}", question=f"Question number {i} about Apple?", **override)
            )
        )
    return out


def test_set_level_checks() -> None:
    problems = validate_set(_questions(3))
    assert any("only 3 questions" in p for p in problems)
    assert any("multi_hop" in p for p in problems) and any("unanswerable" in p for p in problems)
    dup = [Question.model_validate(q()), Question.model_validate(q())]
    got = validate_set(dup)
    assert any("duplicate id" in p for p in got) and any("duplicate question" in p for p in got)


def test_load_questions_reports_line_numbers(tmp_path: Path) -> None:
    f = tmp_path / "q.jsonl"
    f.write_text(json.dumps(q()) + "\n\n" + json.dumps(q(id="bad")) + "\n")
    with pytest.raises(ValueError, match=r"q\.jsonl:3"):
        load_questions(f)
    f.write_text(json.dumps(q()) + "\n")
    assert len(load_questions(f)) == 1


def seed(conn: psycopg.Connection[dict[str, object]]) -> None:
    conn.execute("INSERT INTO companies (id, cik, ticker, name) VALUES (1,'1','AAPL','Apple Inc.')")
    conn.execute(
        "INSERT INTO filings (id, company_id, accession, form, fiscal_year, period_end)"
        " VALUES (1,1,'a25','10-K',2025,'2025-09-27')"
    )
    conn.execute(
        "INSERT INTO chunks (filing_id, company_id, fiscal_year, section, page, chunk_index,"
        " content, content_hash, token_count)"
        " VALUES (1,1,2025,'Item 2',19,0,'Headquarters: Cupertino, California','h',5)"
    )
    conn.execute(
        "INSERT INTO xbrl_facts (company_id, filing_id, concept, fiscal_year, fiscal_period,"
        " period_end, unit, value, accession)"
        " VALUES (1,1,'Assets',2025,'FY','2025-09-27','USD',359241000000,'a25')"
    )
    conn.commit()


def test_database_resolution(conn: psycopg.Connection[dict[str, object]]) -> None:
    seed(conn)
    good = Question.model_validate(
        q(
            xbrl_refs=[
                {"ticker": "AAPL", "concept": "Assets", "fiscal_year": 2025, "value": 359241000000}
            ]
        )
    )
    assert validate_against_db(conn, [good]) == []

    bad_source = Question.model_validate(q(id="q002", gold_sources=[src(contains="Springfield")]))
    wrong_year = Question.model_validate(q(id="q003", gold_sources=[src(fiscal_year=2024)]))
    bad_value = Question.model_validate(
        q(
            id="q004",
            xbrl_refs=[{"ticker": "AAPL", "concept": "Assets", "fiscal_year": 2025, "value": 1}],
        )
    )
    no_fact = Question.model_validate(
        q(
            id="q005",
            xbrl_refs=[{"ticker": "AAPL", "concept": "Revenues", "fiscal_year": 2025, "value": 1}],
        )
    )
    problems = validate_against_db(conn, [bad_source, wrong_year, bad_value, no_fact])
    assert len(problems) == 4
    assert {p.split()[0] for p in problems} == {"q002", "q003", "q004", "q005"}
