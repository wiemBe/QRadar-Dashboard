"""Versioned Fernet key-rotation semantics (no DB needed)."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from app.security.crypto import Encryptor, KeyRotationError

KEY1 = "sxvVvbfjEG8mA0m2m6b1cQ2E0N4l7rXqO4uJ6c8zY5A="


def test_encrypt_is_versioned_and_roundtrips() -> None:
    enc = Encryptor({1: KEY1}, active_version=1)
    ct = enc.encrypt("qradar-token")
    assert ct.startswith("v1:")
    assert "qradar-token" not in ct
    assert enc.decrypt(ct) == "qradar-token"


def test_old_ciphertext_decrypts_after_rotation() -> None:
    key2 = Fernet.generate_key().decode()
    v1 = Encryptor({1: KEY1}, active_version=1)
    old = v1.encrypt("secret")

    rotated = Encryptor({1: KEY1, 2: key2}, active_version=2)
    assert rotated.encrypt("new").startswith("v2:")
    assert rotated.decrypt(old) == "secret"  # historical key still works
    assert rotated.needs_reencryption(old) is True
    assert rotated.needs_reencryption(rotated.encrypt("new")) is False


def test_missing_key_version_raises_clear_error() -> None:
    key2 = Fernet.generate_key().decode()
    v1 = Encryptor({1: KEY1}, active_version=1)
    old = v1.encrypt("secret")
    # Keyring rotated forward and v1 dropped: must fail loudly, not silently.
    only_v2 = Encryptor({2: key2}, active_version=2)
    with pytest.raises(KeyRotationError, match="version 1"):
        only_v2.decrypt(old)


def test_legacy_unversioned_value_still_decrypts() -> None:
    # A value written before rotation existed has no v<N>: prefix.
    raw = Fernet(KEY1.encode()).encrypt(b"legacy").decode()
    enc = Encryptor({1: KEY1}, active_version=1)
    assert enc.decrypt(raw) == "legacy"
    assert enc.needs_reencryption(raw) is True


def test_empty_keyring_refused() -> None:
    with pytest.raises(RuntimeError, match="not configured"):
        Encryptor({}, active_version=1)


def test_active_version_must_be_in_keyring() -> None:
    with pytest.raises(RuntimeError, match="not in the keyring"):
        Encryptor({1: KEY1}, active_version=2)
