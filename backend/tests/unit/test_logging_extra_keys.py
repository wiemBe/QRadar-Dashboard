"""No `extra=` key may collide with a reserved LogRecord attribute.

`logging.Logger.makeRecord` raises KeyError rather than shadowing a reserved
name, so `extra={"created": 3}` turns a successful collection run into an
exception — but only where the logger is actually enabled for that level.
Under pytest's default configuration the call short-circuits in
`isEnabledFor`, so the bug is invisible to unit tests and appears in the
deployed stack, which configures INFO logging.

This walks the source rather than exercising each call site, because the point
is to catch the next one, not the two that have already been fixed.
"""

from __future__ import annotations

import ast
import logging
import pathlib

import pytest

APP = pathlib.Path(__file__).resolve().parents[2] / "app"

#: Attributes `logging` sets on every record. Derived rather than hardcoded, so
#: this cannot drift from the interpreter it runs on.
RESERVED = set(
    logging.LogRecord("n", logging.INFO, "p", 1, "m", None, None).__dict__
) | {"message", "asctime"}


def _extra_keys_in(path: pathlib.Path) -> list[tuple[int, str]]:
    """Every literal key passed as `extra={...}` to a logging call."""
    found: list[tuple[int, str]] = []
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg != "extra" or not isinstance(kw.value, ast.Dict):
                continue
            for key in kw.value.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    found.append((key.lineno, key.value))
    return found


ALL_SOURCES = sorted(APP.rglob("*.py"))


@pytest.mark.parametrize("path", ALL_SOURCES, ids=lambda p: str(p.relative_to(APP)))
def test_no_reserved_logrecord_keys_in_extra(path: pathlib.Path) -> None:
    collisions = [
        f"{path.relative_to(APP)}:{line} uses reserved LogRecord attribute {key!r}"
        for line, key in _extra_keys_in(path)
        if key in RESERVED
    ]
    assert not collisions, (
        "logging.makeRecord raises KeyError on these, failing the operation "
        "being logged:\n  " + "\n  ".join(collisions)
    )


def test_the_guard_detects_a_real_collision(tmp_path: pathlib.Path) -> None:
    """The scanner must actually find something, or it proves nothing."""
    sample = tmp_path / "sample.py"
    sample.write_text('logger.info("x", extra={"created": 1, "safe": 2})\n')
    keys = {k for _, k in _extra_keys_in(sample)}
    assert "created" in keys
    assert "created" in RESERVED
    assert "safe" not in RESERVED


def test_logging_really_rejects_a_reserved_key() -> None:
    """Pin the behaviour this guard exists to prevent."""
    logger = logging.getLogger("test.reserved.key")
    logger.setLevel(logging.INFO)
    logger.addHandler(logging.NullHandler())
    with pytest.raises(KeyError):
        logger.info("boom", extra={"created": 1})
