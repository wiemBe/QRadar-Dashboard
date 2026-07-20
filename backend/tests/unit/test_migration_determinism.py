"""Committed migrations must be deterministic and self-contained.

These are static checks over the migration sources, so they need no database.

A migration that builds its schema from live model metadata is not a historical
record: changing a model retroactively changes what an old revision creates, so
a fresh install and an upgraded install stop converging. 0001 had exactly that
defect and was frozen to explicit DDL; these tests keep it frozen.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

VERSIONS_DIR = Path(__file__).resolve().parents[2] / "alembic" / "versions"

# Dynamic metadata shortcuts. Any of these inside a committed revision makes the
# revision's effect depend on the application's *current* models.
FORBIDDEN_CALLS = ("create_all", "drop_all", "reflect")


def _migration_files() -> list[Path]:
    files = sorted(VERSIONS_DIR.glob("[0-9]*.py"))
    assert files, f"no migration files found in {VERSIONS_DIR}"
    return files


def _code_only(path: Path) -> ast.Module:
    """Parse the module so docstrings and comments are excluded from checks.

    Both migrations *discuss* create_all() in prose explaining why it is gone;
    a plain substring search would flag that text.
    """
    return ast.parse(path.read_text())


@pytest.mark.parametrize("path", _migration_files(), ids=lambda p: p.name)
def test_migration_uses_no_dynamic_metadata_shortcut(path: Path) -> None:
    tree = _code_only(path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in FORBIDDEN_CALLS, (
                f"{path.name} calls {node.func.attr}(); committed migrations must use "
                "explicit, reviewable operations so the revision cannot drift with the models"
            )


@pytest.mark.parametrize("path", _migration_files(), ids=lambda p: p.name)
def test_migration_does_not_import_application_models(path: Path) -> None:
    tree = _code_only(path)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("app.models"):
            pytest.fail(
                f"{path.name} imports {node.module}; a historical revision must not depend "
                "on current application models"
            )
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("app.models"), (
                    f"{path.name} imports {alias.name}; a historical revision must not "
                    "depend on current application models"
                )


def test_baseline_revision_declares_only_phase2_hypertables() -> None:
    """0001 is the Phase 2 baseline: the Phase 3 hypertables belong to 0002.

    If a Phase 3 hypertable ever reappears in 0001, the freeze has been undone.
    """
    source = (VERSIONS_DIR / "0001_initial_schema.py").read_text()
    for phase3_table in ("rule_health_snapshot", "detection_coverage_snapshot"):
        assert phase3_table not in source, (
            f"0001 references {phase3_table}, which is a Phase 3 object added by 0002"
        )
