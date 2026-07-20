"""Application settings.

Everything is sourced from the environment. No secret is ever hardcoded, and
`Settings` refuses to construct an unsafe production configuration.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import (
    AliasChoices,
    Field,
    SecretStr,
    ValidationInfo,
    field_validator,
    model_validator,
)
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
        # Fields carrying a validation_alias (qradar_host accepts both
        # QRADAR_BASE_URL and QRADAR_HOST) must stay constructible by their
        # own name too, or Settings(qradar_host=...) silently falls back to
        # the default instead of validating what the caller passed.
        populate_by_name=True,
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
    # QRADAR_BASE_URL is the preferred name; QRADAR_HOST is accepted for
    # compatibility with existing deployments.
    qradar_host: str = Field(
        default="", validation_alias=AliasChoices("QRADAR_BASE_URL", "QRADAR_HOST")
    )
    qradar_sec_token: SecretStr = SecretStr("")
    #: Path to a file holding the SEC token. Preferred over QRADAR_SEC_TOKEN:
    #: an env var is readable from /proc, leaks into `docker inspect` and into
    #: any crash reporter that dumps the environment. A file can be mounted
    #: read-only and mode-restricted. When both are set, the file wins.
    qradar_api_token_file: str | None = None
    qradar_api_version: str = "20.0"
    qradar_verify_ssl: bool = True
    qradar_ca_bundle: str | None = None
    #: Tolerate an appliance certificate with no Authority Key Identifier.
    #: Relaxes an RFC 5280 labelling assertion only — chain, expiry and
    #: hostname verification stay on. See app/providers/tls.py.
    qradar_tls_allow_missing_aki: bool = False

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

    # --- Offense collection (Phase 3) ---------------------------------------
    offense_collection_interval_seconds: int = Field(default=300, ge=60)
    # Pages of offenses walked per run. Bounds a single collection cycle so a
    # large offense backlog cannot turn one tick into an unbounded crawl.
    offense_max_pages: int = Field(default=20, ge=1, le=500)
    offense_page_size: int = Field(default=100, ge=1, le=1000)
    # Hard ceiling on how far back a first run or a gap-fill reaches. Never
    # unlimited: a fresh install must not try to ingest years of offenses.
    offense_max_backfill_hours: int = Field(default=168, ge=1, le=8760)
    # An open offense older than this is "exceeding SLA" on the dashboard.
    offense_sla_hours: int = Field(default=24, ge=1)
    # Magnitude at or above which an offense counts as critical.
    offense_critical_magnitude: int = Field(default=7, ge=1, le=10)

    # --- Rule inventory + health (Phase 3) ----------------------------------
    rule_collection_interval_seconds: int = Field(default=3600, ge=60)
    rule_max_pages: int = Field(default=20, ge=1, le=500)
    # Evaluation window for "has this rule fired recently".
    rule_health_window_days: int = Field(default=30, ge=1, le=365)
    # A rule newer than this is INSUFFICIENT_DATA, never INACTIVE: a detection
    # deployed yesterday has not had a chance to fire yet.
    rule_health_grace_period_days: int = Field(default=7, ge=0, le=365)
    # Minimum observed history before an inactivity verdict is trustworthy.
    rule_health_min_history_days: int = Field(default=3, ge=0, le=365)
    # Silence beyond this many days, for a rule that has fired before.
    rule_health_inactivity_days: int = Field(default=14, ge=1, le=365)
    # Firings per day above which a rule is NOISY, unless the rule carries its
    # own expected_daily_firings.
    rule_health_noisy_daily_firings: float = Field(default=500.0, ge=1.0)
    # Consecutive evaluations agreeing before the stored verdict flips. Rule
    # health drives alerting, and a rule that fires irregularly would otherwise
    # oscillate between HEALTHY and INACTIVE every evaluation.
    rule_health_flap_threshold: int = Field(default=2, ge=1, le=10)
    # Bumped whenever classification logic changes meaning, so historical
    # snapshots stay interpretable. Not operator-tunable in practice.
    rule_health_logic_version: int = 1

    # --- Detection coverage (Phase 3) ---------------------------------------
    coverage_evaluation_interval_seconds: int = Field(default=3600, ge=60)
    # Inferred mappings below this confidence are recorded but never counted
    # toward coverage.
    coverage_min_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    coverage_logic_version: int = 1

    # --- API guardrails -----------------------------------------------------
    api_default_page_size: int = Field(default=50, ge=1, le=500)
    api_max_page_size: int = Field(default=200, ge=1, le=1000)
    # Widest date range any list/history endpoint will honour.
    api_max_range_days: int = Field(default=365, ge=1)

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

    # ------------------------------------------------------------- secrets
    def resolve_qradar_token(self) -> SecretStr:
        """Return the SEC token, preferring the file over the environment.

        Read on demand rather than cached at startup so rotating the mounted
        file takes effect on the next provider build without a restart. Errors
        name the path but never the contents.
        """
        if self.qradar_api_token_file:
            path = Path(self.qradar_api_token_file)
            try:
                # .strip() because an editor-authored token file almost always
                # ends in a newline, and a trailing \n in a SEC header is a 401
                # that looks exactly like a wrong token.
                token = path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise ValueError(
                    f"QRADAR_API_TOKEN_FILE could not be read: {path} ({exc.strerror})"
                ) from None
            if not token:
                raise ValueError(f"QRADAR_API_TOKEN_FILE is empty: {path}")
            return SecretStr(token)
        return self.qradar_sec_token

    @property
    def has_qradar_token(self) -> bool:
        return bool(self.qradar_api_token_file or self.qradar_sec_token.get_secret_value())

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
        if self.qradar_provider is ProviderKind.REST and not self.has_qradar_token:
            problems.append(
                "QRADAR_API_TOKEN_FILE or QRADAR_SEC_TOKEN is required when QRADAR_PROVIDER=rest"
            )
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
