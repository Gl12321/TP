from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from backend.app.infrastructure.schemas import Input


SourcePassword = Annotated[
    str, StringConstraints(strip_whitespace=False, min_length=1, max_length=1024)
]


class SourceCreate(Input):
    name: str = Field(min_length=1, max_length=160)
    host: str = Field(min_length=1, max_length=253, pattern=r"^[A-Za-z0-9.:-]+$")
    port: int = Field(default=5432, ge=1, le=65535)
    database: str = Field(min_length=1, max_length=128)
    username: str = Field(min_length=1, max_length=128)
    password: SourcePassword
    schemas: list[str] = Field(min_length=1, max_length=30)
    ssl_mode: Literal["require", "verify-full", "disable"] = "require"
    reader_ids: list[str] | None = Field(default=None, max_length=1000)

    @field_validator("schemas")
    @classmethod
    def validate_schemas(cls, value):
        for name in value:
            if (
                not name
                or len(name.encode()) > 63
                or "\x00" in name
                or name == "information_schema"
                or name.startswith("pg_")
            ):
                raise ValueError("Invalid reporting schema")
        return sorted(set(value))


class TablePolicy(Input):
    schema_name: str = Field(alias="schema", min_length=1, max_length=63)
    name: str = Field(min_length=1, max_length=63)
    columns: list[str] = Field(min_length=1, max_length=200)
    store_column: str | None = None
    shared: bool = False

    @model_validator(mode="after")
    def check_boundary(self):
        if self.shared == bool(self.store_column):
            raise ValueError("Choose a store column or explicitly mark a shared dimension")
        if self.store_column and self.store_column not in self.columns:
            raise ValueError("Store column must be included in allowed columns")
        if len(self.columns) != len(set(self.columns)):
            raise ValueError("Duplicate column")
        return self


class PolicyUpdate(Input):
    tables: list[TablePolicy] = Field(max_length=100)


class SourceUpdate(Input):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    host: str | None = Field(
        default=None, min_length=1, max_length=253, pattern=r"^[A-Za-z0-9.:-]+$"
    )
    port: int | None = Field(default=None, ge=1, le=65535)
    database: str | None = Field(default=None, min_length=1, max_length=128)
    username: str | None = Field(default=None, min_length=1, max_length=128)
    password: SourcePassword | None = None
    schemas: list[str] | None = Field(default=None, min_length=1, max_length=30)
    ssl_mode: Literal["require", "verify-full", "disable"] | None = None
    enabled: bool | None = None
    reader_ids: list[str] | None = Field(default=None, max_length=1000)

    @field_validator("schemas")
    @classmethod
    def validate_schemas(cls, value):
        return SourceCreate.validate_schemas(value) if value is not None else None


class IssueCreate(Input):
    source_id: str
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=10000)
    assignee_id: str | None = None
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=100)


class IssueUpdate(Input):
    status: Literal["open", "in_progress", "resolved"] | None = None
    assignee_id: str | None = None
    resolution: str | None = Field(default=None, min_length=1, max_length=10000)


class IssueCommentCreate(Input):
    body: str = Field(min_length=1, max_length=10000)
