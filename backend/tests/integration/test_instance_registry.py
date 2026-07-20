"""Instance registration against a real database.

The property that matters operationally: running setup twice must converge on
one console, not two. `name` is unique in the schema, so a duplicate would
raise — these assert the intended outcome happens instead.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select, text

from app.models.instance import QRadarInstance
from app.services.instance_registry import (
    InstanceNotFound,
    InstanceRegistry,
    read_token_file,
)

pytestmark = pytest.mark.integration

CONSOLE = "https://qradar.example"


async def _count(session) -> int:
    return await session.scalar(select(func.count()).select_from(QRadarInstance))


class TestIdempotentRegistration:
    async def test_first_registration_creates(self, db_session) -> None:
        result = await InstanceRegistry(db_session).register(
            name="lab", console_host=CONSOLE, sec_token="tok"
        )
        assert result.created is True
        assert result.action == "created"
        assert await _count(db_session) == 1

    async def test_second_registration_updates_rather_than_duplicating(
        self, db_session
    ) -> None:
        registry = InstanceRegistry(db_session)
        first = await registry.register(name="lab", console_host=CONSOLE, sec_token="tok")
        second = await registry.register(name="lab", console_host=CONSOLE, sec_token="tok")

        assert second.created is False
        assert second.action == "updated"
        assert second.instance.id == first.instance.id
        assert await _count(db_session) == 1

    async def test_re_registration_applies_changed_fields(self, db_session) -> None:
        registry = InstanceRegistry(db_session)
        await registry.register(name="lab", console_host=CONSOLE, api_version="20.0")
        result = await registry.register(
            name="lab", console_host="https://moved.example", api_version="29.0"
        )
        assert result.instance.console_host == "https://moved.example"
        assert result.instance.api_version == "29.0"

    async def test_omitting_the_token_preserves_the_stored_one(self, db_session) -> None:
        """Changing the API version must not silently wipe credentials."""
        registry = InstanceRegistry(db_session)
        await registry.register(name="lab", console_host=CONSOLE, sec_token="original")
        await registry.register(name="lab", console_host=CONSOLE, api_version="29.0")

        instance = await registry.get_by_name("lab")
        assert instance.sec_token == "original"

    async def test_supplying_a_token_rotates_it(self, db_session) -> None:
        registry = InstanceRegistry(db_session)
        await registry.register(name="lab", console_host=CONSOLE, sec_token="original")
        await registry.register(name="lab", console_host=CONSOLE, sec_token="rotated")

        assert (await registry.get_by_name("lab")).sec_token == "rotated"

    async def test_distinct_names_are_distinct_instances(self, db_session) -> None:
        registry = InstanceRegistry(db_session)
        await registry.register(name="lab-a", console_host=CONSOLE)
        await registry.register(name="lab-b", console_host=CONSOLE)
        assert await _count(db_session) == 2


class TestTokenEncryption:
    async def test_token_is_not_stored_in_plaintext(self, db_session) -> None:
        """EncryptedString must actually encrypt at rest."""
        await InstanceRegistry(db_session).register(
            name="lab", console_host=CONSOLE, sec_token="super-secret-token"
        )
        await db_session.commit()

        # Raw SQL deliberately: selecting through the mapped column would run
        # EncryptedString's result processor and hand back the decrypted value,
        # so the assertion would pass without proving anything.
        stored = await db_session.scalar(
            text("SELECT sec_token FROM qradar_instance WHERE name = :n"), {"n": "lab"}
        )
        assert stored is not None
        assert "super-secret-token" not in str(stored)

    async def test_the_token_still_round_trips(self, db_session) -> None:
        registry = InstanceRegistry(db_session)
        await registry.register(
            name="lab", console_host=CONSOLE, sec_token="super-secret-token"
        )
        await db_session.commit()
        db_session.expunge_all()

        assert (await registry.get_by_name("lab")).sec_token == "super-secret-token"


class TestValidation:
    async def test_plaintext_console_is_refused_for_rest(self, db_session) -> None:
        with pytest.raises(ValueError, match="https"):
            await InstanceRegistry(db_session).register(
                name="lab", console_host="http://qradar.example"
            )

    async def test_disabling_verification_is_refused(self, db_session) -> None:
        with pytest.raises(ValueError, match="verify_ssl"):
            await InstanceRegistry(db_session).register(
                name="lab", console_host=CONSOLE, verify_ssl=False
            )

    async def test_a_missing_ca_bundle_is_refused_before_writing(
        self, db_session
    ) -> None:
        """Fail before the insert, so a bad path leaves no half-registered row."""
        with pytest.raises(ValueError, match="CA bundle does not exist"):
            await InstanceRegistry(db_session).register(
                name="lab", console_host=CONSOLE, ca_bundle_path="/nope/ca.pem"
            )
        assert await _count(db_session) == 0

    async def test_mock_provider_may_use_a_non_https_host(self, db_session) -> None:
        result = await InstanceRegistry(db_session).register(
            name="lab", console_host="mock", provider_kind="mock"
        )
        assert result.created is True


class TestLookup:
    async def test_get_by_name_raises_a_named_error(self, db_session) -> None:
        with pytest.raises(InstanceNotFound, match="absent"):
            await InstanceRegistry(db_session).get_by_name("absent")

    async def test_list_all_is_ordered_by_name(self, db_session) -> None:
        registry = InstanceRegistry(db_session)
        for name in ("zulu", "alpha", "mike"):
            await registry.register(name=name, console_host=CONSOLE)
        assert [i.name for i in await registry.list_all()] == ["alpha", "mike", "zulu"]


class TestReadTokenFile:
    def test_reads_and_strips(self, tmp_path) -> None:
        f = tmp_path / "t.sec"
        f.write_text("  abc-123\n")
        assert read_token_file(f) == "abc-123"

    def test_missing_file_names_the_path_not_the_contents(self, tmp_path) -> None:
        with pytest.raises(ValueError, match="could not be read"):
            read_token_file(tmp_path / "nope.sec")

    def test_empty_file_is_refused(self, tmp_path) -> None:
        f = tmp_path / "t.sec"
        f.write_text("\n\n")
        with pytest.raises(ValueError, match="empty"):
            read_token_file(f)
