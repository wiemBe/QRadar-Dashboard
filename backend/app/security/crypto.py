"""Field-level encryption with migration-safe key rotation.

Design
------
Sensitive columns (QRadar tokens, webhook URLs) are encrypted at rest with
Fernet. To rotate keys without a data migration we use a **versioned keyring**:

  * Every ciphertext is stored as ``v<N>:<fernet-token>`` where ``N`` is the key
    version that produced it.
  * Decryption reads the version prefix and selects that key, so ciphertext
    written under any historical key still decrypts as long as the key remains
    in the keyring.
  * New writes always use the *active* version (``ENCRYPTION_KEY_VERSION``).

Why not a per-row key_version column?
  The version travels *with* the value inside the token, so no schema change and
  no per-table column are needed — adding a new encrypted column later
  automatically participates in rotation. The prefix is authenticated only in
  the sense that a wrong key fails to decrypt; the prefix itself just selects
  the key.

Rotation procedure (operational, zero-downtime)
  1. Generate a new key. Add it to ``ENCRYPTION_KEYS_JSON`` as ``{"2": "<key>"}``
     while keeping version 1 present. Deploy. Nothing re-encrypts yet; both keys
     decrypt.
  2. Set ``ENCRYPTION_KEY_VERSION=2`` (and make key 2 the primary
     ``ENCRYPTION_KEY`` if desired). New writes use v2.
  3. Run ``python -m app.security.crypto --reencrypt`` (or the admin task) to
     rewrite existing v1 values as v2.
  4. Once nothing references v1, drop it from the keyring.

A legacy value with **no** ``v<N>:`` prefix is assumed to be version 1, so
databases written before rotation existed keep working.
"""

from __future__ import annotations

import re

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import Settings, get_settings

_PREFIX_RE = re.compile(r"^v(\d+):(.*)$", re.DOTALL)


class KeyRotationError(RuntimeError):
    """Raised when a value cannot be decrypted with any known key."""


class Encryptor:
    """Versioned Fernet encryptor built from the configured keyring."""

    def __init__(self, keyring: dict[int, str], active_version: int) -> None:
        if not keyring:
            raise RuntimeError("ENCRYPTION_KEY is not configured; cannot handle encrypted columns")
        if active_version not in keyring:
            raise RuntimeError(
                f"active encryption key version {active_version} is not in the keyring"
            )
        self._active_version = active_version
        self._fernets: dict[int, Fernet] = {
            ver: Fernet(key.encode()) for ver, key in keyring.items()
        }
        self._active = self._fernets[active_version]
        # MultiFernet lets a legacy (unprefixed) token be tried against all keys.
        self._multi = MultiFernet(list(self._fernets.values()))

    def encrypt(self, plaintext: str) -> str:
        token = self._active.encrypt(plaintext.encode()).decode()
        return f"v{self._active_version}:{token}"

    def decrypt(self, stored: str) -> str:
        match = _PREFIX_RE.match(stored)
        if match:
            version = int(match.group(1))
            token = match.group(2)
            fernet = self._fernets.get(version)
            if fernet is None:
                raise KeyRotationError(
                    f"ciphertext was written with key version {version}, which is not in the "
                    "current keyring; add it back to ENCRYPTION_KEYS_JSON to decrypt"
                )
            try:
                return fernet.decrypt(token.encode()).decode()
            except InvalidToken as exc:
                raise KeyRotationError(
                    f"failed to decrypt with key version {version}; key material may be wrong"
                ) from exc

        # No version prefix: legacy value. Try every key.
        try:
            return self._multi.decrypt(stored.encode()).decode()
        except InvalidToken as exc:
            raise KeyRotationError(
                "failed to decrypt an unversioned (legacy) value with any known key"
            ) from exc

    def needs_reencryption(self, stored: str) -> bool:
        match = _PREFIX_RE.match(stored)
        if not match:
            return True  # legacy, unprefixed
        return int(match.group(1)) != self._active_version


_encryptor: Encryptor | None = None


def get_encryptor(settings: Settings | None = None) -> Encryptor:
    """Process-wide encryptor. Rebuilt only if explicitly reset (tests)."""
    global _encryptor
    if _encryptor is None:
        settings = settings or get_settings()
        _encryptor = Encryptor(settings.encryption_keyring(), settings.encryption_key_version)
    return _encryptor


def reset_encryptor() -> None:
    """Test hook: force the keyring to be rebuilt from current settings."""
    global _encryptor
    _encryptor = None
