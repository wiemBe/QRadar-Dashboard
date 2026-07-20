"""Application settings.

Everything is sourced from the environment. No secret is ever hardcoded, and
`Settings` refuses to construct an unsafe production configuration.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class ProviderKind(StrEnum):
    MOCK = "mock"
    REST = "rest"
    MCP = "mcp"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- core ---------------------------------------------------------------
    environment: Environment = Environment.DEVELOPMENT
    debug: bool = False
    log_level: str = "INFO"
    api_prefix: str = "/api/v1"

    secret_key: SecretStr = SecretStr("")
    encryption_key: SecretStr = SecretStr("")

    # --- database -----------------------------------------------------------
    postgres_user: str = "qradar_obs"
    postgres_password: SecretStr = SecretStr("")
    postgres_db: str = "qradar_obs"
    postgres_host: str = "postgres"
    postgres_port: int = 5432

    # --- redis / celery -----------------------------------------------------
    redis_url: str = "redis://redis:6379/0"
    celery_broker_url: str = "redis://redis:6379/1"
    celery_result_backend: str = "redis://redis:6379/2"

    # --- provider selection -------------------------------------------------
    qradar_provider: ProviderKind = ProviderKind.MOCK

    # --- QRadar REST --------------------------------------------------------
    qradar_host: str = ""
    qradar_sec_token: SecretStr = SecretStr("")
    qradar_api_version: str = "20.0"
    qradar_verify_ssl: bool = True
    qradar_ca_bundle: str | None = None

    # --- Ariel guardrails ---------------------------------------------------
    ariel_max_concurrent_searches: int = Field(default=3, ge=1, le=20)
    ariel_default_timeout_seconds: int = Field(default=300, ge=10)
    ariel_max_timeout_seconds: int = Field(default=900, ge=10)
    ariel_max_time_range_hours: int = Field(default=168, ge=1)
    ariel_max_result_rows: int = Field(default=10_000, ge=1)
    ariel_poll_interval_seconds: float = Field(default=3.0, ge=0.5)
    ariel_max_retries: int = Field(default=3, ge=0, le=10)
    ariel_retry_base_seconds: float = Field(default=2.0, ge=0.1)
    ariel_retry_max_seconds: float = Field(default=60.0, ge=1.0)
    # Global cap across all instances, plus the per-instance cap above.
    ariel_global_max_concurrent_searches: int = Field(default=6, ge=1, le=50)

    # --- Scheduled-search driver --------------------------------------------
    # Most searches dispatched in one scheduler cycle, so a backlog can never
    # turn a single tick into an unbounded burst of Ariel searches.
    search_max_dispatch_per_cycle: int = Field(default=10, ge=1, le=200)
    # Consecutive failed runs before a scheduled search itself raises an alert.
    # A search that has quietly stopped producing results is exactly the blind
    # spot this platform exists to surface, so it is alertable in its own right.
    search_failure_alert_after: int = Field(default=3, ge=1)

    # --- Retention (TimescaleDB) --------------------------------------------
    # NEVER hardcode destructive retention. All default to None = retention
    # DISABLED (data kept indefinitely). Set explicitly to opt into dropping
    # old chunks. Applied by app.services.timescale, never by a schema migration.
    retention_enabled: bool = False
    retention_log_source_metric_days: int | None = None
    retention_search_result_metric_days: int | None = None
    retention_rule_metric_days: int | None = None
    retention_offense_snapshot_days: int | None = None
    compression_after_days: int | None = None

    # --- Encryption / key rotation ------------------------------------------
    # Active key used for new encryptions. Legacy single-key deployments set
    # ENCRYPTION_KEY only; rotation adds ENCRYPTION_KEYS (JSON {version: key})
    # and bumps ENCRYPTION_KEY_VERSION. Decryption tries all known keys.
    encryption_key_version: int = 1
    # JSON object mapping version(int as string) -> fernet key. Optional.
    encryption_keys_json: str = ""

    # --- Metric collection --------------------------------------------------
    collection_interval_seconds: int = Field(default=300, ge=60)
    collection_batch_size: int = Field(default=50, ge=1)
    collection_max_backfill_intervals: int = Field(default=12, ge=0)
    collection_advisory_lock_namespace: int = 4711

    # --- Baselines ----------------------------------------------------------
    baseline_min_samples: int = Field(default=8, ge=1)
    baseline_lookback_days: int = Field(default=28, ge=1)
    baseline_mad_floor_ratio: float = Field(default=0.1, ge=0.0)

    # --- Anomaly hysteresis (defaults; overridable per type/criticality) ----
    anomaly_open_after_intervals: int = Field(default=2, ge=1)
    anomaly_resolve_after_intervals: int = Field(default=3, ge=1)
    anomaly_deviation_threshold: float = Field(default=3.5, ge=0.0)

    # --- Notifications ------------------------------------------------------
    notify_max_retries: int = Field(default=5, ge=0, le=20)
    notify_retry_base_seconds: float = Field(default=2.0, ge=0.1)
    notify_retry_max_seconds: float = Field(default=300.0, ge=1.0)
    notify_send_recovery: bool = True
    notify_per_channel_rate: str = "30/minute"

    # --- MCP ----------------------------------------------------------------
    mcp_enabled: bool = False
    mcp_base_url: str = "http://qradar-mcp:5000"
    mcp_timeout_seconds: int = 60

    # --- auth ---------------------------------------------------------------
    auth_provider: Literal["local", "oidc"] = "local"
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: SecretStr = SecretStr("")
    oidc_redirect_uri: str = ""

    # --- rate limiting ------------------------------------------------------
    rate_limit_default: str = "120/minute"
    rate_limit_search_execution: str = "10/minute"

    # --- LLM (interface only) ----------------------------------------------
    llm_enabled: bool = False
    llm_provider: str = "null"
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = "claude-opus-4-8"
    llm_allow_autonomous_actions: bool = False

    # ------------------------------------------------------------------ urls
    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:"
            f"{self.postgres_password.get_secret_value()}@"
            f"{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def sync_database_url(self) -> str:
        """Alembic and Celery use the sync driver."""
        return (
            f"postgresql+psycopg://{self.postgres_user}:"
            f"{self.postgres_password.get_secret_value()}@"
            f"{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def is_production(self) -> bool:
        return self.environment is Environment.PRODUCTION

    def encryption_keyring(self) -> dict[int, str]:
        """All known Fernet keys by version, active version included.

        Legacy deployments provide only ENCRYPTION_KEY (version becomes
        `encryption_key_version`). Rotation adds ENCRYPTION_KEYS_JSON. The union
        is used to build a MultiFernet so ciphertext written under any historical
        key still decrypts.
        """
        import json

        keys: dict[int, str] = {}
        primary = self.encryption_key.get_secret_value()
        if primary:
            keys[self.encryption_key_version] = primary
        if self.encryption_keys_json.strip():
            try:
                extra = json.loads(self.encryption_keys_json)
            except json.JSONDecodeError as exc:
                raise ValueError("ENCRYPTION_KEYS_JSON is not valid JSON") from exc
            for ver, key in extra.items():
                keys[int(ver)] = key
        return keys

    def retention_days(self) -> dict[str, int | None]:
        """Configured retention per hypertable. None = disabled (non-destructive)."""
        if not self.retention_enabled:
            return {}
        return {
            "log_source_metric": self.retention_log_source_metric_days,
            "search_result_metric": self.retention_search_result_metric_days,
            "rule_metric": self.retention_rule_metric_days,
            "offense_snapshot": self.retention_offense_snapshot_days,
        }

    # ----------------------------------------------------------- validation
    @field_validator("qradar_host")
    @classmethod
    def _reject_plaintext_qradar(cls, v: str) -> str:
        if v and not v.startswith("https://"):
            raise ValueError(
                "QRADAR_HOST must use https:// — a SIEM token must never cross plaintext HTTP"
            )
        return v

    @field_validator("ariel_max_timeout_seconds")
    @classmethod
    def _timeout_ordering(cls, v: int, info: ValidationInfo) -> int:
        default = info.data.get("ariel_default_timeout_seconds")
        if default is not None and v < default:
            raise ValueError("ARIEL_MAX_TIMEOUT_SECONDS must be >= ARIEL_DEFAULT_TIMEOUT_SECONDS")
        return v

    @model_validator(mode="after")
    def _production_hardening(self) -> Settings:
        """Refuse to boot a production deployment with a development posture.

        These are startup failures rather than warnings: every one of them is a
        finding an auditor would raise, and a warning in a log nobody reads is
        not a control.
        """
        if not self.is_production:
            return self

        problems: list[str] = []

        if self.debug:
            problems.append("DEBUG must be false in production")
        if not self.qradar_verify_ssl:
            problems.append("QRADAR_VERIFY_SSL must be true in production")
        if len(self.secret_key.get_secret_value()) < 32:
            problems.append("SECRET_KEY must be at least 32 characters")
        if not self.encryption_key.get_secret_value():
            problems.append("ENCRYPTION_KEY is required to encrypt stored credentials")
        if self.qradar_provider is ProviderKind.MOCK:
            problems.append("QRADAR_PROVIDER=mock is not permitted in production")
        if self.qradar_provider is ProviderKind.REST and not (
            self.qradar_sec_token.get_secret_value()
        ):
            problems.append("QRADAR_SEC_TOKEN is required when QRADAR_PROVIDER=rest")
        if self.llm_allow_autonomous_actions:
            problems.append(
                "LLM_ALLOW_AUTONOMOUS_ACTIONS must be false; autonomous response is not implemented"
            )

        if problems:
            raise ValueError("Unsafe production configuration:\n  - " + "\n  - ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
