"""Offense response schemas.

Regression cover for a 500 that only appeared against a real appliance: the
mock provider produced no numeric grouping keys, so nothing exercised the
coercion until live data arrived.
"""

from __future__ import annotations

import pytest

from app.schemas.offense import CountEntry


class TestCountEntryKeyCoercion:
    def test_string_key_is_unchanged(self) -> None:
        assert CountEntry(key="OPEN", count=3).key == "OPEN"

    def test_integer_key_is_coerced(self) -> None:
        """`distribution(..., "magnitude")` groups by an integer column.

        Pydantic's str type rejects an int outright, so before this coercion
        GET /offenses/analytics returned 500 for any instance with offenses.
        """
        assert CountEntry(key=4, count=7).key == "4"  # type: ignore[arg-type]

    def test_rule_id_key_is_coerced(self) -> None:
        """top_entities("rule_ids") yields ints; ("usernames") yields strings.

        One endpoint therefore feeds both shapes into the same model.
        """
        assert CountEntry(key=100051, count=7).key == "100051"  # type: ignore[arg-type]

    def test_none_key_becomes_empty_not_the_word_none(self) -> None:
        """Unassigned offenses group under NULL.

        Rendering that as the literal "None" would be indistinguishable from an
        analyst actually named None, and would show up in the UI as a real
        assignee.
        """
        assert CountEntry(key=None, count=7).key == ""  # type: ignore[arg-type]

    @pytest.mark.parametrize("count", [0, 1, 10_000])
    def test_count_is_preserved(self, count: int) -> None:
        assert CountEntry(key="x", count=count).count == count
