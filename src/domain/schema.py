from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class TableRef:
    schema: str
    name: str

    @property
    def key(self) -> str:

        return f"{self.schema}.{self.name}"


@dataclass(frozen=True)
class Column:
    name: str
    data_type: str
    nullable: bool = True
    comment: str | None = None


@dataclass(frozen=True)
class ForeignKey:
    columns: tuple[str, ...]
    target: TableRef
    target_columns: tuple[str, ...]


@dataclass(frozen=True)
class TableSchema:
    ref: TableRef
    columns: tuple[Column, ...]
    primary_key: tuple[str, ...] = ()
    foreign_keys: tuple[ForeignKey, ...] = ()
    ddl: str = ""
    description: str = ""

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)


@dataclass(frozen=True)
class RetrievedTable:
    table: TableSchema
    score: float = 0.0
