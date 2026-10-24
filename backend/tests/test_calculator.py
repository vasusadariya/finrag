import math
from typing import Any

import pytest

from app.tools.calculator import CalcError, calculate, calculator_tool


@pytest.mark.parametrize(
    "expr, want",
    [
        ("1+2*3", 7),
        ("(1+2)*3", 9),
        ("2**10", 1024),
        ("-5 + +3", -2),
        ("10/4", 2.5),
        ("7 // 2", 3),
        ("7 % 4", 3),
        ("383285000000 - 394328000000", -11_043_000_000),
        ("pct_change(100, 125)", 25),
        ("pct_change(200, 150)", -25),
        ("pct_change(-100, -50)", 50),
        ("cagr(100, 121, 2)", 10),
        ("pct_of(25, 200)", 12.5),
        ("ratio(9, 3)", 3),
        ("round(pct_change(383285, 391035), 2)", 2.02),
        ("max(1, 5, 3) - min(4, 2)", 3),
        ("sqrt(16) + abs(-2)", 6),
    ],
)
def test_valid(expr: str, want: float) -> None:
    assert calculate(expr) == pytest.approx(want)


@pytest.mark.parametrize(
    "expr",
    [
        "",
        "   ",
        "1/0",
        "pct_change(0, 5)",
        "cagr(100, 50, 0)",
        "cagr(-1, 5, 2)",
        "pct_of(1, 0)",
        "2**1000",
        "9**9**9",
        "(-8)**0.5",
        "sqrt(-1)",
        "log(0)",
        "1e999",
        "1 +",
        "383,285",
        "x + 1",
        "'a' * 3",
        "[1,2]",
        "True + 1",
        "None",
        "1 if 1 else 2",
        "lambda: 1",
        "__import__('os').system('id')",
        "open('/etc/passwd')",
        "().__class__",
        "abs.__call__(1)",
        "eval('1')",
        "pct_change(a=1, b=2)",
        "round(1.5, 1, 2, 3)",
        "1 << 5",
        "1 < 2",
        "(1)" * 300,
        "1+" * 150 + "1",
    ],
)
def test_rejected(expr: str) -> None:
    with pytest.raises(CalcError):
        calculate(expr)


def test_results_are_always_finite_floats() -> None:
    for expr in ["10**100", "1/3", "cagr(1, 1000000, 0.5)"]:
        assert math.isfinite(calculate(expr))


def test_tool_never_raises_and_shapes_output() -> None:
    assert calculator_tool({"expression": "2+2"}) == {"ok": True, "expression": "2+2", "result": 4}
    bad_args: list[dict[str, Any]] = [
        {},
        {"expression": 5},
        {"expression": None},
        {"expression": "1/0"},
    ]
    for bad in bad_args:
        out = calculator_tool(bad)
        assert out["ok"] is False and isinstance(out["error"], str)
