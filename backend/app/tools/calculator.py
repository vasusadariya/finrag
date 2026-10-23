"""Safe arithmetic for the agent: growth rates, CAGR, margins and ratios. Never uses eval."""

import ast
import math
import operator
from collections.abc import Callable
from typing import Any

MAX_EXPR_CHARS = 500
MAX_NODES = 200
MAX_EXPONENT = 100
MAX_ABS = 1e300


class CalcError(ValueError):
    """The expression is invalid or not allowed; the message is shown to the model."""


def pct_change(old: float, new: float) -> float:
    """Percent change from ``old`` to ``new``."""
    if old == 0:
        raise CalcError("pct_change: the starting value is zero")
    return (new - old) / abs(old) * 100


def cagr(start: float, end: float, years: float) -> float:
    """Compound annual growth rate in percent."""
    if years <= 0:
        raise CalcError("cagr: years must be positive")
    if start <= 0 or end <= 0:
        raise CalcError("cagr: start and end must be positive")
    return float(((end / start) ** (1 / years) - 1) * 100)


def ratio(a: float, b: float) -> float:
    """a / b."""
    return a / b


def pct_of(part: float, whole: float) -> float:
    """part as a percent of whole (margins, shares)."""
    if whole == 0:
        raise CalcError("pct_of: the whole is zero")
    return part / whole * 100


FUNCTIONS: dict[str, Callable[..., float]] = {
    "abs": lambda x: float(abs(x)),
    "round": lambda x, n=0: float(round(x, int(n))),
    "min": min,
    "max": max,
    "sqrt": math.sqrt,
    "log": math.log,
    "pct_change": pct_change,
    "cagr": cagr,
    "ratio": ratio,
    "pct_of": pct_of,
}

BINOPS: dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
}
UNARYOPS: dict[type[ast.unaryop], Callable[[float], float]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _num(v: float) -> float:
    if math.isnan(v) or math.isinf(v) or abs(v) > MAX_ABS:
        raise CalcError("result is not a finite, reasonably sized number")
    return v


def _eval(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant):
        value: object = node.value
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise CalcError("only numbers are allowed")
        return _num(value)
    if isinstance(node, ast.BinOp):
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow):
            if abs(right) > MAX_EXPONENT:
                raise CalcError(f"exponent larger than {MAX_EXPONENT} is not allowed")
            if left < 0 and right != int(right):
                raise CalcError("fractional power of a negative number")
            return _num(left**right)
        op = BINOPS.get(type(node.op))
        if op is None:
            raise CalcError(f"operator {type(node.op).__name__} is not allowed")
        return _num(op(left, right))
    if isinstance(node, ast.UnaryOp):
        uop = UNARYOPS.get(type(node.op))
        if uop is None:
            raise CalcError(f"operator {type(node.op).__name__} is not allowed")
        return _num(uop(_eval(node.operand)))
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS:
            raise CalcError(f"function not allowed; use one of {sorted(FUNCTIONS)}")
        if node.keywords:
            raise CalcError("keyword arguments are not supported")
        args = [_eval(a) for a in node.args]
        try:
            return _num(FUNCTIONS[node.func.id](*args))
        except CalcError:
            raise
        except (TypeError, ValueError, ZeroDivisionError, OverflowError) as e:
            raise CalcError(f"{node.func.id}: {e}") from e
    raise CalcError(f"{type(node).__name__} is not allowed; use numbers, + - * / ** and functions")


def calculate(expression: str) -> float:
    """Evaluate a numeric expression. Raises CalcError for anything invalid."""
    expr = expression.strip() if isinstance(expression, str) else ""
    if not expr:
        raise CalcError("expression is empty")
    if len(expr) > MAX_EXPR_CHARS:
        raise CalcError(f"expression longer than {MAX_EXPR_CHARS} characters")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise CalcError(f"syntax error: {e.msg}") from e
    if sum(1 for _ in ast.walk(tree)) > MAX_NODES:
        raise CalcError("expression too complex")
    try:
        return _eval(tree)
    except ZeroDivisionError as e:
        raise CalcError("division by zero") from e
    except OverflowError as e:
        raise CalcError("number too large") from e


def calculator_tool(args: dict[str, Any]) -> dict[str, Any]:
    """Tool entry point: always returns a dict, never raises."""
    expression = args.get("expression")
    try:
        if not isinstance(expression, str):
            raise CalcError("'expression' must be a string")
        value = calculate(expression)
    except CalcError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "expression": expression, "result": value}
