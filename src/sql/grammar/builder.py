from pathlib import Path
from typing import Sequence

from src.domain.query import GenerationRefusal
from src.domain.schema import TableRef, TableSchema


def quote_identifier(name: str) -> str:

    if not name or "\x00" in name:
        raise ValueError("SQL identifiers must be nonempty and contain no NUL.")
    return '"' + name.replace('"', '""') + '"'


def _terminal(value: str) -> str:
    escapes = {'"': '\\"', "\\": "\\\\", "\n": "\\n", "\r": "\\r", "\t": "\\t"}


    encoded = "".join(
        escapes.get(character, f"\\x{ord(character):02x}" if ord(character) < 32 else character)
        for character in value
    )
    return '"' + encoded + '"'


class SQLGrammarBuilder:


    @staticmethod
    def aliases(tables: Sequence[TableSchema]) -> dict[TableRef, str]:
        refs = [table.ref for table in tables]
        if len(set(refs)) != len(refs):
            raise ValueError("Duplicate table references in SQL context.")
        return {ref: f"t{index}" for index, ref in enumerate(sorted(refs))}

    @classmethod
    def build(cls, tables: Sequence[TableSchema], *, allow_refusal: bool = False) -> str:
        if not tables:
            raise ValueError("Cannot build a SQL grammar without tables.")
        aliases = cls.aliases(tables)
        table_by_ref = {table.ref: table for table in tables}
        source_rules: list[str] = []
        column_rules: list[str] = []
        stars: list[str] = []
        for ref, alias in aliases.items():
            table = table_by_ref[ref]
            if not table.columns:
                raise ValueError(f"Table {ref.key} has no visible columns.")
            names = [column.name for column in table.columns]
            if len(set(names)) != len(names):
                raise ValueError(f"Duplicate column names in {ref.key}.")
            path = f"{quote_identifier(ref.schema)}.{quote_identifier(ref.name)}"
            source_rules.append(_terminal(f"{path} AS {alias}"))
            column_rules.extend(
                _terminal(f"{alias}.{quote_identifier(name)}") for name in sorted(names)
            )
            stars.append(_terminal(f"{alias}.*"))

        directory = Path(__file__).parent
        statement = 'select ws ";"?'
        if allow_refusal:
            alternatives = " | ".join(_terminal(item.value) for item in GenerationRefusal)
            statement = f"({statement} | {alternatives})"
        fragments = [f"root ::= ws {statement} ws"]
        fragments.extend(
            (directory / name).read_text(encoding="utf-8").rstrip()
            for name in ("lexical.gbnf", "expressions.gbnf", "select.gbnf")
        )
        fragments.extend([
            "source ::= " + " | ".join(source_rules),
            "column ::= " + " | ".join(column_rules),
            "source-star ::= " + " | ".join(stars),
        ])
        return "\n\n".join(fragments) + "\n"
