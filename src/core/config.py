import os
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache
from urllib.parse import quote

from src.core.presets import BASE_DIR, load_config, resolve_models

ENV_FILE = os.path.join(BASE_DIR, ".env")

class Settings(BaseSettings):
    DB_USER: str
    DB_PASSWORD: str
    DB_HOST: str = "db"
    DB_PORT: int = 5432
    DB_NAME: str
    DB_READ_USER: str | None = None
    DB_READ_PASSWORD: str | None = None
    API_TOKEN: str | None = None

    MODEL_PRESET: str
    MAX_UPLOAD_BYTES: int = Field(gt=0)
    MAX_RESULT_ROWS: int = Field(gt=0)
    MAX_RESULT_BYTES: int = Field(gt=0)
    SQL_TIMEOUT_MS: int = Field(gt=0)
    SQL_LOCK_TIMEOUT_MS: int = Field(gt=0)
    LLM_TIMEOUT_SECONDS: int = Field(gt=0)
    MIN_FREE_MEMORY_MB: int = Field(gt=0)
    MAX_CORRECTIONS: int = Field(ge=0, le=10)
    RETRIEVAL_TOP_K: int = Field(gt=0)
    CONTEXT_MAX_TABLES: int = Field(gt=0)
    EMBEDDING_BATCH_SIZE: int = Field(gt=0)
    RERANKER_BATCH_SIZE: int = Field(gt=0)
    EVENT_QUEUE_SIZE: int = Field(gt=0)

    VECTOR_DB_PATH: str = str(BASE_DIR / "data"/ "chromadb")
    SQLITE_PATH: str = str(BASE_DIR / "data" / "sqlite_storage")

    MODELS: dict = Field(default_factory=dict)

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding='utf-8',
        extra="ignore",
        hide_input_in_errors=True,
    )

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings,
                                   dotenv_settings, file_secret_settings):
        def yaml_settings():
            config = load_config()
            unknown = set(config["settings"]) - (set(cls.model_fields) - {"MODEL_PRESET", "MODELS"})
            if unknown:
                raise ValueError("Неизвестные настройки: " + ", ".join(sorted(unknown)))
            return {"MODEL_PRESET": config["model"], **config["settings"]}

        return init_settings, env_settings, dotenv_settings, file_secret_settings, yaml_settings

    @model_validator(mode="after")
    def configure_models(self):
        if not self.MODEL_PRESET:
            self.MODEL_PRESET = load_config()["model"]
        if not self.MODELS:
            self.MODELS = resolve_models(load_config(), self.MODEL_PRESET)
        return self

    @property
    def db_url_async(self) -> str:
        return self._db_url("asyncpg", self.DB_USER, self.DB_PASSWORD)

    @property
    def db_url_sync(self) -> str:
        return self._db_url("psycopg2", self.DB_USER, self.DB_PASSWORD)

    @property
    def db_read_url(self) -> str:
        if not self.DB_READ_USER or self.DB_READ_PASSWORD is None:
            raise ValueError("DB_READ_USER and DB_READ_PASSWORD must specify a separate read-only role")
        if self.DB_READ_USER == self.DB_USER:
            raise ValueError("The SQL reader must use a different role from the importer")
        return self._db_url("asyncpg", self.DB_READ_USER, self.DB_READ_PASSWORD)

    def _db_url(self, driver: str, user: str, password: str) -> str:
        host = self.DB_HOST
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return (f"postgresql+{driver}://{quote(user, safe='')}:{quote(password, safe='')}"
                f"@{host}:{self.DB_PORT}/{quote(self.DB_NAME, safe='')}")


@lru_cache()
def get_settings() -> Settings:
    return Settings()
