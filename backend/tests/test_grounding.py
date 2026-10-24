import pytest

from app.agent.grounding import extract_numbers, find_ungrounded, source_numbers

SRC = source_numbers(
    [
        {"ok": True, "rows": [[391035000000, 383285000000]], "sql": "SELECT 1 LIMIT 50"},
        {"ok": True, "result": 2.0219, "expression": "pct_change(383285, 391035)"},
        {"ok": True, "result": -11.04},
        {"ok": True, "results": [{"text": "Net sales were $391.0 billion; 164,000 employees."}]},
        "How did revenue grow 10% in 2024?",
    ]
)


@pytest.mark.parametrize(
    "answer",
    [
        "Revenue was $391.0 billion [sql1].",
        "Revenue was $391 billion.",
        "Revenue was 391,035,000,000 dollars.",
        "Revenue was $391.04B.",
        "Revenue was $391,035 million.",
        "Growth was 2.0% [sql1].",
        "Growth was 2.02 percent.",
        "It fell 11.0% year over year.",
        "It declined by 11%.",
        "The company had 164,000 employees [c1].",
        "Revenue rose 10% as you noted.",
        "In fiscal 2024 (FY2024), see Item 1A and the 10-K [c1].",
        "Three segments and 2 regions in Q3.",
        "1. First point\n2. Second point",
    ],
)
def test_grounded_answers_pass(answer: str) -> None:
    assert find_ungrounded(answer, SRC) == []


@pytest.mark.parametrize(
    "answer, bad",
    [
        ("Revenue was $395 billion.", ["$395 billion"]),
        ("Revenue was $391.5 billion.", ["$391.5 billion"]),
        ("Growth was 3.1%.", ["3.1%"]),
        ("There were 170,000 employees.", ["170,000"]),
        ("Net income was $93.7 billion [sql1].", ["$93.7 billion"]),
        ("Margin was 25%.", ["25%"]),
        ("Revenue 391 million.", ["391 million"]),
    ],
)
def test_invented_numbers_are_flagged(answer: str, bad: list[str]) -> None:
    assert find_ungrounded(answer, SRC) == bad


def test_percent_accepts_fraction_form() -> None:
    assert find_ungrounded("Ratio was 25%.", source_numbers([{"result": 0.25}])) == []


def test_citation_markers_and_years_are_not_numbers() -> None:
    assert find_ungrounded("Revenue [sql12] in 2023 and 2024 [c34].", []) == []
    assert [n.value for n in extract_numbers("$1.5 trillion and 3 million")] == [1.5e12, 3e6]


def test_negative_and_scale_parsing() -> None:
    n = extract_numbers("a loss of -$2.5bn")[0]
    assert n.value == -2.5e9 and n.half == pytest.approx(0.05e9)


def test_no_sources_flags_every_real_number() -> None:
    assert find_ungrounded("Revenue was $5 billion and up 7.5%.", []) == ["$5 billion", "7.5%"]


def test_sql_text_and_calculator_input_are_not_evidence() -> None:
    src = source_numbers([{"sql": "SELECT 777 LIMIT 50", "expression": "pct_change(555, 600)"}])
    assert src == []


def test_calculator_inputs_must_be_grounded() -> None:
    from app.agent.grounding import find_ungrounded_inputs

    src = source_numbers([{"rows": [[100, 150]]}])
    assert find_ungrounded_inputs("pct_change(100, 150)", src) == []
    assert find_ungrounded_inputs("pct_of(100, 150) * 100 / 4", src) == []
    assert find_ungrounded_inputs("pct_change(100, 175)", src) == ["175"]


def test_unit_scaled_tool_output_grounds_scaled_answer() -> None:
    # SQL that divides by 1e9 returns 36.01; a filing table "in millions" holds 209586
    src = source_numbers([{"rows": [[36.01, 33.68]]}, {"results": [{"text": "Net sales 209,586"}]}])
    assert find_ungrounded("Net income fell from $36.01 billion to $33.68 billion.", src) == []
    assert find_ungrounded("Net sales were $209,586 million.", src) == []
    assert find_ungrounded("Net income was $36.5 billion.", src) == ["$36.5 billion"]
    assert find_ungrounded("Net sales were $209,587 million.", src) == ["$209,587 million"]
