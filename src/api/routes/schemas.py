import asyncio
from pathlib import Path, PureWindowsPath
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from src.api.dependencies import Services, authorize, services
from src.core.concurrency import finish_in_thread
from src.core.logging import get_logger
from src.database.identifiers import validate_schema_name
from src.domain.query import QueryError

router = APIRouter(dependencies=[Depends(authorize)])
logger = get_logger("api.schemas")


def upload_schema_name(filename: str | None) -> str:
    if not filename or Path(filename).name != filename or PureWindowsPath(filename).name != filename:
        raise QueryError("invalid_filename", "Недопустимое имя файла.")
    if Path(filename).suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
        raise QueryError("invalid_file", "Нужен файл SQLite: .db, .sqlite или .sqlite3.")
    name = Path(filename).stem
    try:
        return validate_schema_name(name)
    except ValueError as exc:
        raise QueryError("invalid_schema", str(exc)) from exc


@router.post("/load_schema")
async def load_schema(files: list[UploadFile] = File(...), resources: Services = Depends(services)):
    if not 1 <= len(files) <= 8:
        raise HTTPException(422, "За один запрос допускается от 1 до 8 файлов.")
    loaded = []
    try:
        names = [upload_schema_name(file.filename) for file in files]
        if len(set(names)) != len(names):
            raise QueryError("duplicate_schema", "Файлы в одном запросе должны иметь разные имена схем.")
        async with resources.gate.enter():
            storage = Path(resources.settings.SQLITE_PATH).resolve()
            storage.mkdir(parents=True, exist_ok=True)
            for file, name in zip(files, names):
                target = storage / f"{uuid4().hex}.sqlite"
                try:
                    size = 0
                    with target.open("xb") as output:
                        while chunk := await file.read(1024 * 1024):
                            size += len(chunk)
                            if size > resources.settings.MAX_UPLOAD_BYTES:
                                raise QueryError("upload_too_large", "Превышен допустимый размер файла.")
                            await finish_in_thread(output.write, chunk)
                    loop = asyncio.get_running_loop()

                    def invalidate():

                        asyncio.run_coroutine_threadsafe(resources.catalog.mark_dirty(name), loop).result()

                    try:
                        await finish_in_thread(resources.importer.migrate_db, name, str(target),
                                               before_replace=invalidate)
                    except ValueError as exc:
                        raise QueryError("invalid_sqlite", str(exc)) from exc
                    await resources.catalog.index_schema(name)
                    loaded.append(name)
                finally:
                    target.unlink(missing_ok=True)
        return {"status": "success", "loaded_schemas": loaded}
    except QueryError as exc:
        code = 409 if exc.code == "busy" else 413 if exc.code == "upload_too_large" else 400
        raise HTTPException(code, {"code": exc.code, "message": str(exc), "loaded_schemas": loaded}) from exc
    except Exception as exc:
        logger.exception("Schema import failed")
        raise HTTPException(500, {"code": "import_failed", "loaded_schemas": loaded,
                                 "message": "Не удалось завершить импорт. Проверьте состояние схем и индекса."}) from exc
    finally:
        for file in files:
            await file.close()


@router.get("/schema_show")
async def schema_show(resources: Services = Depends(services)):
    async with resources.gate.enter():
        return {"schemas": await resources.schema_reader.list_schemas(),
                "unindexed_schemas": sorted(resources.catalog.dirty_schemas)}


@router.delete("/drop_all_schemas")
async def drop_all_schemas(resources: Services = Depends(services)):
    async with resources.gate.enter():
        for schema in await resources.schema_reader.list_schemas():
            await resources.catalog.mark_dirty(schema)
        dropped = await resources.schema_reader.drop_all_schemas()
        await resources.catalog.reset_store()
        return {"status": "success", "dropped_schemas": dropped}


@router.post("/schemas/reindex")
async def reindex(resources: Services = Depends(services)):
    async with resources.gate.enter():

        schemas = await resources.schema_reader.list_schemas()
        await resources.catalog.reset_store(schemas_to_reindex=schemas)
        await resources.catalog.index_all_schemas()
        return {"status": "success"}
