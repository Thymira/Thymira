"""Bound and syntax-check model-supplied Python source without executing it."""

from __future__ import annotations

MAX_PYTHON_SOURCE_BYTES = 100_000
MAX_PYTHON_SOURCE_LINES = 10_000
_LITERAL_NEWLINE_ESCAPE = r"\n"


def validate_python_source(source: str) -> str:
    """Return source unchanged after enforcing limits and checking Python syntax."""
    if len(source) > MAX_PYTHON_SOURCE_BYTES:
        raise ValueError(f"Python source exceeds {MAX_PYTHON_SOURCE_BYTES} characters")
    if len(source.encode("utf-8")) > MAX_PYTHON_SOURCE_BYTES:
        raise ValueError(f"Python source exceeds {MAX_PYTHON_SOURCE_BYTES} UTF-8 bytes")
    line_count = source.count("\n") + source.count("\r") - source.count("\r\n") + 1
    if line_count > MAX_PYTHON_SOURCE_LINES:
        raise ValueError(f"Python source exceeds {MAX_PYTHON_SOURCE_LINES} lines")
    try:
        compile(source, "<run_python>", "exec")
    except SyntaxError as exc:
        line = exc.lineno or 1
        column = exc.offset or 1
        message = f"invalid Python syntax at line {line}, column {column}: {exc.msg}"
        if _is_literal_newline_escape_at_error(exc):
            message += (
                "; literal backslash+n found. Use an actual newline to separate statements; "
                "unchanged input fails. Not approved or run; source unchanged."
            )
        raise ValueError(message) from exc
    except (MemoryError, OverflowError, RecursionError) as exc:
        raise ValueError("Python source exceeds parser resource limits") from exc
    except ValueError as exc:
        raise ValueError(f"invalid Python source: {exc}") from exc
    return source


def _is_literal_newline_escape_at_error(exc: SyntaxError) -> bool:
    """Detect a literal backslash-n at the parser's unexpected-character position."""
    if (
        exc.msg != "unexpected character after line continuation character"
        or exc.text is None
        or exc.offset is None
        or exc.offset < len(_LITERAL_NEWLINE_ESCAPE)
    ):
        return False
    return exc.text[exc.offset - len(_LITERAL_NEWLINE_ESCAPE) : exc.offset] == (
        _LITERAL_NEWLINE_ESCAPE
    )
