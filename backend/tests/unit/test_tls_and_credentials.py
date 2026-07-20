"""TLS context construction, file-backed SEC tokens and per-instance providers.

The security-relevant assertions here are the negative ones: that nothing in
this path can end up not verifying a peer, and that relaxing RFC 5280 strictness
relaxes *only* that.
"""

from __future__ import annotations

import datetime as dt
import ssl
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.core.config import ProviderKind, Settings
from app.models.instance import QRadarInstance
from app.providers.factory import build_provider_for_instance
from app.providers.mock import MockQRadarProvider
from app.providers.qradar_mcp import QRadarMCPProvider
from app.providers.qradar_rest import QRadarRestProvider
from app.providers.tls import build_ssl_context

ENCRYPTION_KEY = "x" * 44


@pytest.fixture(scope="module")
def ca_file(tmp_path_factory: pytest.TempPathFactory) -> str:
    """A real self-signed CA PEM, so load_verify_locations actually parses it."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Local CA")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    path = tmp_path_factory.mktemp("tls") / "ca.pem"
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return str(path)


class TestSSLContext:
    def test_default_context_verifies_the_peer(self) -> None:
        ctx = build_ssl_context()
        assert ctx.verify_mode is ssl.CERT_REQUIRED
        assert ctx.check_hostname is True

    def test_strict_rfc5280_is_on_by_default(self) -> None:
        """The relaxation must be opt-in, never the default posture."""
        ctx = build_ssl_context()
        assert ctx.verify_flags & ssl.VERIFY_X509_STRICT

    def test_allow_missing_aki_clears_only_strict(self) -> None:
        strict = build_ssl_context()
        relaxed = build_ssl_context(allow_missing_aki=True)

        assert not (relaxed.verify_flags & ssl.VERIFY_X509_STRICT)
        # Everything else about the flag word is untouched...
        assert (relaxed.verify_flags | ssl.VERIFY_X509_STRICT) == strict.verify_flags
        # ...and the trust decision itself is unchanged.
        assert relaxed.verify_mode is ssl.CERT_REQUIRED
        assert relaxed.check_hostname is True

    def test_ca_bundle_is_loaded(self, ca_file: str) -> None:
        ctx = build_ssl_context(ca_file)
        subjects = [c["subject"] for c in ctx.get_ca_certs()]
        assert ((("commonName", "Test Local CA"),),) in subjects

    def test_unreadable_ca_bundle_raises(self, tmp_path: Path) -> None:
        with pytest.raises(OSError):
            build_ssl_context(str(tmp_path / "does-not-exist.pem"))

    def test_relaxation_is_logged(
        self, caplog: pytest.LogCaptureFixture, ca_file: str
    ) -> None:
        """An operator must be able to see this was in effect after the fact."""
        with caplog.at_level("WARNING", logger="app.providers.tls"):
            build_ssl_context(ca_file, allow_missing_aki=True)
        assert "Authority Key Identifier" in caplog.text


class TestTokenFile:
    def _settings(self, **kw: object) -> Settings:
        return Settings(encryption_key=ENCRYPTION_KEY, **kw)  # type: ignore[arg-type]

    def test_token_read_from_file(self, tmp_path: Path) -> None:
        f = tmp_path / "t.sec"
        f.write_text("abc-123")
        s = self._settings(qradar_api_token_file=str(f))
        assert s.resolve_qradar_token().get_secret_value() == "abc-123"

    def test_trailing_newline_is_stripped(self, tmp_path: Path) -> None:
        """An editor-authored token file ends in \\n; unstripped that is a 401."""
        f = tmp_path / "t.sec"
        f.write_text("abc-123\n")
        s = self._settings(qradar_api_token_file=str(f))
        assert s.resolve_qradar_token().get_secret_value() == "abc-123"

    def test_file_takes_precedence_over_env_token(self, tmp_path: Path) -> None:
        f = tmp_path / "t.sec"
        f.write_text("from-file")
        s = self._settings(qradar_api_token_file=str(f), qradar_sec_token="from-env")
        assert s.resolve_qradar_token().get_secret_value() == "from-file"

    def test_falls_back_to_env_token(self) -> None:
        s = self._settings(qradar_sec_token="from-env")
        assert s.resolve_qradar_token().get_secret_value() == "from-env"

    def test_missing_file_raises_without_leaking_contents(self, tmp_path: Path) -> None:
        s = self._settings(qradar_api_token_file=str(tmp_path / "nope.sec"))
        with pytest.raises(ValueError, match="could not be read"):
            s.resolve_qradar_token()

    def test_empty_file_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "t.sec"
        f.write_text("   \n")
        s = self._settings(qradar_api_token_file=str(f))
        with pytest.raises(ValueError, match="empty"):
            s.resolve_qradar_token()

    def test_has_qradar_token_covers_both_sources(self, tmp_path: Path) -> None:
        assert not self._settings().has_qradar_token
        assert self._settings(qradar_sec_token="t").has_qradar_token
        # True on the strength of configuration alone — the file need not exist
        # yet, because it is a mount that appears at runtime.
        assert self._settings(qradar_api_token_file=str(tmp_path / "x")).has_qradar_token

    def test_production_accepts_a_token_file_instead_of_an_env_token(
        self, tmp_path: Path
    ) -> None:
        s = Settings(
            environment="production",
            debug=False,
            secret_key="s" * 40,
            encryption_key=ENCRYPTION_KEY,
            qradar_provider=ProviderKind.REST,
            qradar_host="https://qradar.example",
            qradar_api_token_file=str(tmp_path / "t.sec"),
        )
        assert s.is_production


class TestBaseUrlAlias:
    def test_qradar_base_url_env_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("QRADAR_BASE_URL", "https://a.example")
        assert Settings(encryption_key=ENCRYPTION_KEY).qradar_host == "https://a.example"

    def test_legacy_qradar_host_env_name_still_works(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("QRADAR_HOST", "https://b.example")
        assert Settings(encryption_key=ENCRYPTION_KEY).qradar_host == "https://b.example"

    def test_still_constructible_by_field_name(self) -> None:
        """Regression: validation_alias without populate_by_name silently
        dropped the keyword and skipped the https:// validator."""
        with pytest.raises(ValueError, match="https"):
            Settings(qradar_host="http://plaintext.example", encryption_key=ENCRYPTION_KEY)


class TestPerInstanceProvider:
    def _instance(self, **kw: object) -> QRadarInstance:
        defaults: dict[str, object] = {
            "name": "lab",
            "console_host": "https://qradar.example",
            "api_version": "29.0",
            "sec_token": "tok",
            "verify_ssl": True,
            "ca_bundle_path": None,
            "provider_kind": "rest",
            "mcp_base_url": None,
        }
        return QRadarInstance(**{**defaults, **kw})  # type: ignore[arg-type]

    def test_rest_provider_uses_the_stored_instance_details(self) -> None:
        p = build_provider_for_instance(
            self._instance(console_host="https://console.example", api_version="29.0"),
            Settings(encryption_key=ENCRYPTION_KEY),
        )
        assert isinstance(p, QRadarRestProvider)
        assert str(p._client.base_url).rstrip("/") == "https://console.example/api"
        assert p._client.headers["Version"] == "29.0"

    def test_missing_stored_token_is_a_named_error(self) -> None:
        with pytest.raises(ValueError, match="no stored SEC token"):
            build_provider_for_instance(
                self._instance(sec_token=None), Settings(encryption_key=ENCRYPTION_KEY)
            )

    def test_mock_and_mcp_kinds_are_honoured(self) -> None:
        s = Settings(encryption_key=ENCRYPTION_KEY)
        mock = build_provider_for_instance(self._instance(provider_kind="mock"), s)
        mcp = build_provider_for_instance(self._instance(provider_kind="mcp"), s)
        assert isinstance(mock, MockQRadarProvider)
        assert isinstance(mcp, QRadarMCPProvider)

    def test_instance_token_never_appears_in_repr(self) -> None:
        inst = self._instance(sec_token="super-secret-token")
        assert "super-secret-token" not in repr(inst)
