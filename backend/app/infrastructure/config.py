import os
from dataclasses import dataclass, field
from pathlib import Path
import re
from urllib.parse import urlsplit


def env_list(name: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in os.getenv(name, "").split(",") if item.strip())


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    if value.lower() not in {"true", "false"}:
        raise ValueError(f"{name} must be true or false")
    return value.lower() == "true"


@dataclass(frozen=True)
class Settings:
    database_url: str = field(
        default_factory=lambda: os.getenv("APP_DATABASE_URL", "sqlite+aiosqlite:///./razbor.db")
    )
    secret_key: str = field(default_factory=lambda: os.getenv("APP_SECRET_KEY", ""))
    secure_cookies: bool = field(default_factory=lambda: env_bool("APP_SECURE_COOKIES", True))
    allowed_origins: tuple[str, ...] = field(
        default_factory=lambda: env_list("APP_ALLOWED_ORIGINS")
    )
    source_hosts: tuple[str, ...] = field(default_factory=lambda: env_list("APP_SOURCE_HOSTS"))
    bootstrap_token: str = field(default_factory=lambda: os.getenv("APP_BOOTSTRAP_TOKEN", ""))
    session_ttl_seconds: int = 43200
    job_lease_seconds: int = 90
    max_result_rows: int = 1000
    max_result_bytes: int = 4194304
    sql_timeout_ms: int = 30000
    sql_lock_timeout_ms: int = 3000
    max_queued_jobs: int = 32
    max_queued_jobs_per_workspace: int = 8
    max_queued_jobs_per_user: int = 2
    frontend_dist: Path = field(
        default_factory=lambda: Path(os.getenv("APP_FRONTEND_DIST", "frontend/dist"))
    )
    config_path: Path = field(
        default_factory=lambda: Path(os.getenv("APP_CONFIG_PATH", "config.yaml"))
    )

    def validate(self, *, include_secrets=True) -> None:
        if include_secrets and len(self.secret_key) < 32:
            raise ValueError("APP_SECRET_KEY must contain at least 32 characters")
        if include_secrets and not self.bootstrap_token:
            raise ValueError("APP_BOOTSTRAP_TOKEN is required for initial account setup")
        if not self.database_url.startswith(("postgresql+asyncpg://", "sqlite+aiosqlite://")):
            raise ValueError("APP_DATABASE_URL must use postgresql+asyncpg or sqlite+aiosqlite")
        if "*" in self.allowed_origins:
            raise ValueError("APP_ALLOWED_ORIGINS must contain explicit origins")
        if type(self.secure_cookies) is not bool:
            raise ValueError("secure_cookies must be a boolean")
        for origin in self.allowed_origins:
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.path
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("allowed_origins must contain exact HTTP origins without paths")
            parsed.port
        if any(not re.fullmatch(r"[A-Za-z0-9.:-]+", host) for host in self.source_hosts):
            raise ValueError(
                "source_hosts must contain hostnames or IP addresses without ports or schemes"
            )
        for name in (
            "session_ttl_seconds",
            "job_lease_seconds",
            "max_result_rows",
            "max_result_bytes",
            "sql_timeout_ms",
            "sql_lock_timeout_ms",
            "max_queued_jobs",
            "max_queued_jobs_per_workspace",
            "max_queued_jobs_per_user",
        ):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError(f"{name} must be a positive integer")


def load_settings() -> Settings:
    from dataclasses import replace

    settings = Settings()
    options = {}
    if settings.config_path.is_file():
        import yaml

        document = yaml.safe_load(settings.config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(document, dict):
            raise ValueError("Configuration must contain an object")
        application = document.get("application", {})
        limits = document.get("settings", {})
        if not isinstance(application, dict) or not isinstance(limits, dict):
            raise ValueError("application and settings must be objects")
        unknown = set(application) - {
            "secure_cookies",
            "allowed_origins",
            "source_hosts",
            "session_ttl_seconds",
            "job_lease_seconds",
        }
        if unknown:
            raise ValueError("Unknown application options: " + ", ".join(sorted(unknown)))
        allowed_limits = {
            "MAX_RESULT_ROWS",
            "MAX_RESULT_BYTES",
            "SQL_TIMEOUT_MS",
            "SQL_LOCK_TIMEOUT_MS",
            "LLM_TIMEOUT_SECONDS",
            "MIN_FREE_MEMORY_MB",
            "MAX_CORRECTIONS",
            "RETRIEVAL_TOP_K",
            "CONTEXT_MAX_TABLES",
            "EMBEDDING_BATCH_SIZE",
            "RERANKER_BATCH_SIZE",
            "MAX_QUEUED_JOBS",
            "MAX_QUEUED_JOBS_PER_WORKSPACE",
            "MAX_QUEUED_JOBS_PER_USER",
        }
        if set(limits) - allowed_limits:
            raise ValueError("Unknown settings: " + ", ".join(sorted(set(limits) - allowed_limits)))
        for name, value in limits.items():
            if type(value) is not int or value < (0 if name == "MAX_CORRECTIONS" else 1):
                raise ValueError(f"settings.{name} must be a valid integer limit")
        for name in (
            "secure_cookies",
            "allowed_origins",
            "source_hosts",
            "session_ttl_seconds",
            "job_lease_seconds",
        ):
            if name in application:
                options[name] = application[name]
        for name, key in {
            "max_result_rows": "MAX_RESULT_ROWS",
            "max_result_bytes": "MAX_RESULT_BYTES",
            "sql_timeout_ms": "SQL_TIMEOUT_MS",
            "sql_lock_timeout_ms": "SQL_LOCK_TIMEOUT_MS",
            "max_queued_jobs": "MAX_QUEUED_JOBS",
            "max_queued_jobs_per_workspace": "MAX_QUEUED_JOBS_PER_WORKSPACE",
            "max_queued_jobs_per_user": "MAX_QUEUED_JOBS_PER_USER",
        }.items():
            if key in limits:
                options[name] = limits[key]
    for name in (
        "session_ttl_seconds",
        "job_lease_seconds",
        "max_result_rows",
        "max_result_bytes",
        "sql_timeout_ms",
        "sql_lock_timeout_ms",
        "max_queued_jobs",
        "max_queued_jobs_per_workspace",
        "max_queued_jobs_per_user",
    ):
        if "APP_" + name.upper() in os.environ:
            options[name] = int(os.environ["APP_" + name.upper()])
        if name in options and (type(options[name]) is not int or options[name] <= 0):
            raise ValueError(f"{name} must be a positive integer")
    for name in ("allowed_origins", "source_hosts"):
        if "APP_" + name.upper() in os.environ:
            options[name] = env_list("APP_" + name.upper())
        elif name in options:
            if not isinstance(options[name], list) or not all(
                isinstance(value, str) for value in options[name]
            ):
                raise ValueError(f"{name} must be a list")
            options[name] = tuple(options[name])
    if "APP_SECURE_COOKIES" in os.environ:
        options["secure_cookies"] = env_bool("APP_SECURE_COOKIES", True)
    result = replace(settings, **options)
    result.validate(include_secrets=False)
    return result
