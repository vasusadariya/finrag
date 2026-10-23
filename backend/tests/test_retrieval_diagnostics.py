from app.evalkit.dataset import Question
from app.evalkit.retrieval_diagnostics import classify_miss, oracle
from app.retrieval.types import Hit


def question() -> Question:
    return Question.model_validate(
        {
            "id": "q001",
            "question": "Where is Apple headquartered?",
            "type": "factual",
            "tickers": ["AAPL"],
            "gold_answer": "Cupertino",
            "gold_sources": [
                {
                    "ticker": "AAPL",
                    "fiscal_year": 2025,
                    "section": "Item 2",
                    "contains": "Cupertino",
                }
            ],
        }
    )


def hit(content: str, fy: int = 2025, section: str = "Item 2") -> Hit:
    return Hit(1, 0.0, content, "AAPL", "Apple", fy, section, 1, "acc")


def test_classification() -> None:
    q = question()
    assert classify_miss(q, [hit("HQ: Cupertino")]) == "hit"
    assert classify_miss(q, [hit("HQ: Cupertino", fy=2024)]) == "wrong_year_only"
    assert classify_miss(q, [hit("properties in Texas")]) == "right_section_wrong_chunk"
    assert classify_miss(q, [hit("Cupertino", section="Item 1")]) == "wrong_section"
    assert classify_miss(q, []) == "wrong_section"


def test_oracle_filters() -> None:
    q = question()
    f = oracle(True, True)(q)
    assert (f.tickers, f.sections, f.fiscal_years) == (("AAPL",), ("Item 2",), (2025,))
    assert oracle(True, False)(q).fiscal_years == ()
    assert oracle(False, False)(q).sections == ()
