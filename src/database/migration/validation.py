from pathlib import Path
import sqlite3
import time

from src.database.identifiers import quote_identifier


def validate_sqlite_path(path: str | Path, upload_limit_bytes: int) -> Path:
    resolved = Path(path).resolve(strict=True)
    if not resolved.is_file():
        raise ValueError("Источник SQLite должен быть обычным файлом.")
    size = resolved.stat().st_size
    if size < 100 or size > upload_limit_bytes:
        raise ValueError("Размер SQLite-файла не соответствует допустимому диапазону.")
    with resolved.open("rb") as source:
        if source.read(16) != b"SQLite format 3\x00":
            raise ValueError("Файл не является базой SQLite 3.")
    return resolved


def configure_sqlite(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA trusted_schema = OFF")
    connection.execute("PRAGMA foreign_keys = ON")


def validate_sqlite(connection: sqlite3.Connection, *, max_tables: int = 500,
                    max_columns: int = 256, timeout_seconds: float = 30) -> list[str]:
    deadline = time.monotonic() + timeout_seconds
    connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
    try:
        objects = connection.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type = 'table' AND substr(name, 1, 7) <> 'sqlite_' ORDER BY name"
        ).fetchmany(max_tables + 1)
        if not objects or len(objects) > max_tables:
            raise ValueError(f"SQLite должна содержать от 1 до {max_tables} таблиц.")
        for name, definition in objects:
            quote_identifier(name)
            if definition and "CREATE VIRTUAL TABLE" in " ".join(definition.upper().split()):
                raise ValueError("Импорт виртуальных SQLite-таблиц не поддерживается.")

            columns = connection.execute(f"PRAGMA table_xinfo({quote_identifier(name)})").fetchall()
            if not columns or len(columns) > max_columns:
                raise ValueError(f"Недопустимое число колонок в таблице {name!r}.")
            for column in columns:
                quote_identifier(column[1])
                if column[6]:
                    raise ValueError("Вычисляемые и скрытые колонки SQLite пока не поддерживаются.")
            for index in connection.execute(f"PRAGMA index_list({quote_identifier(name)})"):
                index_name = index[1]
                if not index_name.startswith("sqlite_"):
                    quote_identifier(index_name)


                index_columns = connection.execute(
                    'PRAGMA index_xinfo("' + index_name.replace('"', '""') + '")'
                )
                for item in index_columns:
                    if item[5] and (item[1] == -2 or (item[4] or "BINARY").upper() != "BINARY"):
                        raise ValueError("Индексы с выражениями или особыми COLLATE SQLite не поддерживаются.")
        checks = connection.execute("PRAGMA integrity_check(1)").fetchall()
        if checks != [("ok",)]:
            raise ValueError("Проверка целостности SQLite завершилась ошибкой.")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("В SQLite обнаружены нарушенные внешние ключи.")
        return [name for name, _ in objects]
    except sqlite3.DatabaseError as error:
        raise ValueError("Не удалось проверить SQLite: повреждение, неподдерживаемая схема или таймаут.") from error
    finally:
        connection.set_progress_handler(None, 0)
