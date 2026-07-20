"""The MCP audit sink: what reaches AuditLog, and what must never."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.providers.audit_sink import make_audit_sink


class _FakeSession:
    """Captures record_audit's arguments without a database."""

    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, entry: Any) -> None:
        self.added.append(entry)

    async def flush(self) -> None:
        return None


@pytest.fixture
def session() -> _FakeSession:
    return _FakeSession()


def _record(**overrides: Any) -> dict:
    base = {
        "action": "mcp.tool_call",
        "actor": "collector",
        "object_type": "mcp_tool",
        "object_id": "list_offenses",
        "outcome": "SUCCESS",
        "detail": {
            "tool": "list_offenses",
            "duration_ms": 12,
            "argument_keys": ["limit"],
            "error": None,
            "provider": "mcp",
        },
    }
    base.update(overrides)
    return base


class TestWhatIsStored:
    async def test_a_record_is_written(self, session: _FakeSession) -> None:
        await make_audit_sink(session)(_record())  # type: ignore[arg-type]
        assert len(session.added) == 1
        entry = session.added[0]
        assert entry.action == "mcp.tool_call"
        assert entry.actor == "collector"
        assert entry.object_id == "list_offenses"
        assert entry.outcome == "SUCCESS"

    @pytest.mark.parametrize(
        "outcome", ["SUCCESS", "FAILURE", "DENIED", "UNKNOWN_TOOL"]
    )
    async def test_every_outcome_is_audited(
        self, session: _FakeSession, outcome: str
    ) -> None:
        """A denial must be as auditable as a success — more so."""
        await make_audit_sink(session)(_record(outcome=outcome))  # type: ignore[arg-type]
        assert session.added[0].outcome == outcome

    async def test_instance_and_correlation_are_recorded(
        self, session: _FakeSession
    ) -> None:
        instance_id = uuid.uuid4()
        sink = make_audit_sink(session, instance_id=instance_id, correlation_id="abc")  # type: ignore[arg-type]
        await sink(_record())
        detail = session.added[0].detail
        assert detail["instance_id"] == str(instance_id)
        assert detail["correlation_id"] == "abc"

    async def test_correlation_id_is_generated_when_absent(
        self, session: _FakeSession
    ) -> None:
        """Records must never be uncorrelated.

        Asserting truthiness is not enough: `redact` masks token-shaped values,
        and a UUID is token-shaped, so a masked id is still a non-empty string
        that passes a careless check while being useless for correlation.
        """
        await make_audit_sink(session)(_record())  # type: ignore[arg-type]
        cid = session.added[0].detail["correlation_id"]
        assert cid and "redacted" not in cid
        assert len(cid) >= 24

    async def test_calls_sharing_a_sink_share_a_correlation_id(
        self, session: _FakeSession
    ) -> None:
        sink = make_audit_sink(session)  # type: ignore[arg-type]
        await sink(_record())
        await sink(_record(object_id="list_rules"))
        a, b = session.added
        assert a.detail["correlation_id"] == b.detail["correlation_id"]
        # Two masked ids are also "equal"; assert they are real.
        assert "redacted" not in a.detail["correlation_id"]

    async def test_a_token_under_a_normal_key_is_still_redacted(
        self, session: _FakeSession
    ) -> None:
        """The identifier-key exemption must not have widened.

        A QRadar SEC token is UUID-shaped, so if the exemption ever applied by
        value rather than by key name, real tokens would start surviving.
        """
        record = _record()
        record["detail"]["provider"] = "8fc1e20b-2494-458e-8eef-000000000000"
        await make_audit_sink(session)(record)  # type: ignore[arg-type]
        assert session.added[0].detail["provider"] == "***redacted***"

    async def test_duration_and_argument_key_names_survive(
        self, session: _FakeSession
    ) -> None:
        await make_audit_sink(session)(_record())  # type: ignore[arg-type]
        detail = session.added[0].detail
        assert detail["duration_ms"] == 12
        assert detail["argument_keys"] == ["limit"]


class TestWhatIsNeverStored:
    async def test_unvetted_detail_keys_are_dropped(
        self, session: _FakeSession, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An allowlist, so a future provider change cannot silently start
        persisting a response body or a stack trace."""
        record = _record()
        record["detail"].update(
            {
                "response_body": {"offenses": [{"id": 1}]},
                "sec_token": "super-secret",
                "traceback": "Traceback (most recent call last)...",
                "raw_event": "user=admin password=hunter2",
            }
        )
        with caplog.at_level("WARNING", logger="app.providers.audit_sink"):
            await make_audit_sink(session)(record)  # type: ignore[arg-type]

        detail = session.added[0].detail
        for forbidden in ("response_body", "sec_token", "traceback", "raw_event"):
            assert forbidden not in detail

        serialized = str(detail)
        assert "super-secret" not in serialized
        assert "hunter2" not in serialized
        assert "Traceback" not in serialized
        # And the drop is visible to an operator.
        assert "dropped unvetted MCP audit detail keys" in caplog.text

    async def test_argument_values_are_not_present(
        self, session: _FakeSession
    ) -> None:
        """The provider passes key names only; assert the sink keeps it that way."""
        await make_audit_sink(session)(_record())  # type: ignore[arg-type]
        assert "argument_values" not in session.added[0].detail
