"""Production hardening guards and encrypted-column round-trip."""

from __future__ import annotations

import pytest

from app.core.config import Environment, ProviderKind, Settings


def _prod(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "environment": Environment.PRODUCTION,
        "debug": False,
        "qradar_verify_ssl": True,
        "secret_key": "x" * 40,
        "encryption_key": "sxvVvbfjEG8mA0m2m6b1cQ2E0N4l7rXqO4uJ6c8zY5A=",
        "qradar_provider": ProviderKind.REST,
        "qradar_sec_token": "a-real-token",
        "qradar_host": "https://qradar.example",
    }
    base.update(overrides)
    return base


def test_valid_production_config_boots() -> None:
    Settings(**_prod())  # must not raise


def test_production_rejects_debug() -> None:
    with pytest.raises(ValueError, match="DEBUG"):
        Settings(**_prod(debug=True))


def test_production_rejects_mock_provider() -> None:
    with pytest.raises(ValueError, match="mock"):
        Settings(**_prod(qradar_provider=ProviderKind.MOCK))


def test_production_rejects_disabled_tls() -> None:
    with pytest.raises(ValueError, match="VERIFY_SSL"):
        Settings(**_prod(qradar_verify_ssl=False))


def test_production_rejects_missing_rest_token() -> None:
    with pytest.raises(ValueError, match="QRADAR_SEC_TOKEN"):
        Settings(**_prod(qradar_sec_token=""))


def test_production_rejects_autonomous_llm_actions() -> None:
    with pytest.raises(ValueError, match="AUTONOMOUS"):
        Settings(**_prod(llm_allow_autonomous_actions=True))


def test_plaintext_qradar_host_rejected_any_env() -> None:
    with pytest.raises(ValueError, match="https"):
        Settings(qradar_host="http://qradar.example", encryption_key="x" * 44)


def test_ariel_timeout_ordering_enforced() -> None:
    with pytest.raises(ValueError, match="MAX_TIMEOUT"):
        Settings(
            ariel_default_timeout_seconds=600,
            ariel_max_timeout_seconds=300,
            encryption_key="x" * 44,
        )


def test_encrypted_column_round_trips() -> None:
    """The EncryptedString type must encrypt on bind and decrypt on result."""
    from app.models.base import EncryptedString

    col = EncryptedString()
    ciphertext = col.process_bind_param("super-secret-token", dialect=None)
    assert ciphertext is not None
    assert "super-secret-token" not in ciphertext  # actually encrypted
    assert col.process_result_value(ciphertext, dialect=None) == "super-secret-token"


def test_encrypted_column_handles_none() -> None:
    from app.models.base import EncryptedString

    col = EncryptedString()
    assert col.process_bind_param(None, dialect=None) is None
    assert col.process_result_value(None, dialect=None) is None
