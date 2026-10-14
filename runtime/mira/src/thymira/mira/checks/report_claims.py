"""Pure quantitative-claim matching for the independent A18 report audit."""

from __future__ import annotations

import math
import re

_CLAIM_RE = re.compile(
    r"(?<![\w.])([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:e[+-]?\d+)?)(%|pp)?",
    re.IGNORECASE,
)
"""A signed decimal, grouped decimal or scientific literal outside an identifier."""

_TOLERANCE = 1e-9
"""Absolute tolerance for a claim matching a recorded value exactly."""

_MAX_DECIMALS = 6
"""Cap on the rounding precision used to resolve a claim against a recorded value."""

_MAX_ABS_EXPONENT = 308
"""Largest scientific exponent A18 evaluates without overflowing a finite binary64 value."""


def claim_problems(text: str, recorded: set[float]) -> list[str]:
    """Report every decimal or percentage claim that no recorded value supports."""
    problems: list[str] = []
    reported: set[str] = set()
    for match in _CLAIM_RE.finditer(text):
        digits, unit = match.group(1), match.group(2)
        if "." not in digits and "e" not in digits.casefold() and not unit:
            continue
        if _is_section_number(text, match):
            continue
        token = match.group(0)
        if token in reported:
            continue
        if not _claim_resolves(digits, scaled=unit is not None, recorded=recorded):
            reported.add(token)
            problems.append(f"report cites {token!r}, which no recorded evidence supports")
    return problems


def _is_section_number(text: str, match: re.Match[str]) -> bool:
    """Whether a matched token numbers or references a Markdown section, not a claim.

    `### 3.1 Bivariate Analysis` numbers its own section; no recorded evidence will ever "support"
    that numbering, so treating it as a quantitative claim manufactures a false positive on every
    numbered heading a report writes -- reproduced against a real report's own `### 1.3 ...` and
    `### 2.1 ...` headings.
    """
    line_start = text.rfind("\n", 0, match.start()) + 1
    prefix = text[line_start : match.start()]
    if re.fullmatch(r"#{1,6}[ \t]+", prefix) is not None:
        return True
    return re.search(r"\bsection[ \t]+\Z", prefix, re.IGNORECASE) is not None


def _claim_resolves(digits: str, *, scaled: bool, recorded: set[float]) -> bool:
    """Whether a claim resolves under either numeric convention the recorded evidence may use.

    A percentage claim's own source value may be recorded as a 0-1 proportion (the printed
    literal divided by 100, e.g. a `default_rate` field) or already in percent-native form (the
    literal as printed, e.g. a `..._pct` field) -- both are legitimate, and the report gives no
    reliable signal for which one its own source used, so a claim resolves against either.
    Reproduced against a real report: `support_pct: 27.45` in the recorded evidence never matched
    a cited `27.45%` when only the divide-by-100 reading was tried.
    """
    normalized = digits.replace(",", "")
    parsed = _finite_claim(normalized)
    if parsed is None:
        return False
    raw_value, exponent = parsed
    mantissa = normalized.casefold().partition("e")[0]
    raw_decimals = len(mantissa.partition(".")[2])
    raw_tolerance = 0.5 * 10.0 ** (exponent - min(raw_decimals, _MAX_DECIMALS))
    fraction_value = raw_value / 100
    return _resolves(raw_value, raw_tolerance, recorded) or (
        scaled and _resolves(fraction_value, raw_tolerance / 100, recorded)
    )


def _finite_claim(normalized: str) -> tuple[float, int] | None:
    """Return one finite claim and bounded exponent, or ``None`` when it is unsafe."""
    _mantissa, exponent_marker, exponent_text = normalized.casefold().partition("e")
    exponent = _bounded_exponent(exponent_text) if exponent_marker else 0
    if exponent is None:
        return None
    try:
        value = float(normalized)
    except (OverflowError, ValueError):
        return None
    return (value, exponent) if math.isfinite(value) else None


def _bounded_exponent(text: str) -> int | None:
    """Parse a scientific exponent without constructing an attacker-sized integer or power."""
    unsigned = text.lstrip("+-").lstrip("0") or "0"
    if len(unsigned) > len(str(_MAX_ABS_EXPONENT)):
        return None
    exponent = int(text)
    return exponent if -_MAX_ABS_EXPONENT <= exponent <= _MAX_ABS_EXPONENT else None


def _resolves(value: float, rounding_half_step: float, recorded: set[float]) -> bool:
    """Whether a claim matches a recorded value exactly or at the claim's own precision."""
    for candidate in recorded:
        if abs(candidate - value) <= _TOLERANCE:
            return True
        if abs(candidate - value) <= rounding_half_step + _TOLERANCE:
            return True
    return False
