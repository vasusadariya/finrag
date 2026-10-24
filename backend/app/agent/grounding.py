"""Numbers-only-from-tools: every number in an answer must trace back to a tool result.

The answer's numbers are parsed with their scale words ("$391.0 billion" -> 391.0e9) and each is
compared with the numbers found in the tool outputs (JSON numbers and numbers inside passages)
and in the question. A number matches when it lies within the rounding of how it was written,
so "$391.0 billion" matches 391035000000 but "$391.5 billion" does not. Percent values also match
their fractional form (25% vs 0.25) and sign flips ("fell 11%" vs -11.0).
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

SCALES = {
    "trillion": 1e12, "t": 1e12, "billion": 1e9, "bn": 1e9, "b": 1e9,
    "million": 1e6, "mm": 1e6, "m": 1e6, "thousand": 1e3, "k": 1e3,
}  # fmt: skip
_UNIT = r"(?:trillion|billion|million|thousand|bn|mm|percent|%|[BMTK](?![A-Za-z]))"
NUMBER = re.compile(
    rf"(?<![\w.])(?P<sign>[-−–])?\$?(?P<num>\d{{1,3}}(?:,\d{{3}})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    rf"(?:\s?(?P<unit>{_UNIT}))?",
    re.IGNORECASE,
)
# tokens whose digits are labels, not quantities
_NOISE = re.compile(
    r"\[(?:c|sql)\d+\]|\bFY\s?\d{2,4}\b|\bQ[1-4]\b|\bItem\s+\d+[A-C]?\b|\b10-[KQ]\b|\b8-K\b"
    r"|\bform\s+\d+[-\w]*|^\s*\d+[.)]\s",
    re.IGNORECASE | re.MULTILINE,
)
TOLERANCE_FLOOR = 1e-9
_SIGN_CHARS = "-−–"


@dataclass(frozen=True)
class Num:
    raw: str
    value: float
    half: float  # half a unit in the last written digit, in the same units as value
    percent: bool
    mantissa: float = (
        0.0  # the digits as written, before any scale word ("$36.01 billion" -> 36.01)
    )
    scaled: bool = False  # a scale word (million, billion, ...) was applied
    mantissa_half: float = 0.0  # half a unit in the last written digit of the mantissa


def _parse_numbers(text: str) -> list[Num]:
    out: list[Num] = []
    for m in NUMBER.finditer(_NOISE.sub(" ", text)):
        digits = m.group("num").replace(",", "")
        unit = (m.group("unit") or "").lower()
        scale = SCALES.get(unit, 1.0)
        decimals = len(digits.split(".")[1]) if "." in digits else 0
        value = float(digits) * scale
        if m.group("sign"):
            value = -value
        out.append(
            Num(
                m.group(0).strip(),
                value,
                0.5 * 10**-decimals * scale,
                unit in {"%", "percent"},
                mantissa=-float(digits) if m.group("sign") else float(digits),
                scaled=scale != 1.0,
                mantissa_half=0.5 * 10**-decimals,
            )
        )
    return out


def extract_numbers(text: str) -> list[Num]:
    return _parse_numbers(text)


def _is_exempt(n: Num) -> bool:
    v = abs(n.value)
    plain = n.half == 0.5 and not n.percent and "$" not in n.raw
    plain = plain and not re.search(rf"{_UNIT}$", n.raw.strip(), re.IGNORECASE)
    if plain and v == int(v) and (v <= 10 or 1900 <= v <= 2100):
        return True  # small counts and years
    return False


def _matches(n: Num, source: float) -> bool:
    candidates = [source]
    if n.percent:
        candidates.append(source * 100)  # 0.25 shown as 25%
    for c in candidates:
        for s in (c, -c):
            if abs(n.value - s) <= n.half * (1 + 1e-9) + TOLERANCE_FLOOR * max(1.0, abs(s)):
                return True
    if n.scaled:
        # a tool may already report in the unit the answer states ("36.01" for "$36.01 billion",
        # or a table "in millions": 209,586 -> "$209,586 million")
        half = n.mantissa_half
        for s in (source, -source):
            if abs(n.mantissa - s) <= half * (1 + 1e-9) + TOLERANCE_FLOOR * max(1.0, abs(s)):
                return True
    return False


def source_numbers(payloads: Iterable[Any]) -> list[float]:
    """All numbers in tool outputs / the question: JSON numbers and numbers inside strings."""
    found: list[float] = []

    def walk(x: Any, key: str = "") -> None:
        if isinstance(x, bool) or x is None:
            return
        if isinstance(x, int | float):
            if key not in {"relevance", "count", "row_count"}:
                found.append(float(x))
        elif isinstance(x, str):
            if key not in {"sql", "expression"}:  # SQL text and calculator input are not evidence
                found.extend(n.value for n in _parse_numbers(x))
        elif isinstance(x, dict):
            for k, v in x.items():
                walk(v, str(k))
        elif isinstance(x, list | tuple):
            for v in x:
                walk(v, key)

    for p in payloads:
        walk(p)
    return found


def find_ungrounded(answer: str, sources: list[float]) -> list[str]:
    """Numbers in ``answer`` (as written) that match no source number."""
    bad: list[str] = []
    for n in _parse_numbers(answer):
        if _is_exempt(n) or any(_matches(n, s) for s in sources):
            continue
        bad.append(n.raw)
    return bad


SCALE_CONSTANTS = {100.0, 1000.0, 1e6, 1e9, 1e12, 12.0, 4.0}


def find_ungrounded_inputs(expression: str, sources: list[float]) -> list[str]:
    """Numbers typed into a calculator expression that no tool output or the question supplied.

    Without this, a made-up input would be laundered into a "grounded" calculator result.
    Scale constants (100, 1000, ...) and tiny counts are allowed.
    """
    bad: list[str] = []
    for n in _parse_numbers(expression):
        if abs(n.value) in SCALE_CONSTANTS or _is_exempt(n):
            continue
        if not any(_matches(n, s) for s in sources):
            bad.append(n.raw)
    return bad
