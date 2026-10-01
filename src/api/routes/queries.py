import asyncio
from contextlib import suppress

from anyio import CancelScope
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from src.api.dependencies import Services, authorize, services
from src.api.schemas import QueryRequest
from src.core.logging import get_logger
from src.core.concurrency import finish_task
from src.domain.query import QueryContext, QueryError
from src.sql.results import encode_event

router = APIRouter(dependencies=[Depends(authorize)])
logger = get_logger("api.queries")


@router.post("/question/stream")
async def ask_sql(request: Request, payload: QueryRequest, resources: Services = Depends(services)):
    async def stream():
        queue = asyncio.Queue(maxsize=resources.settings.EVENT_QUEUE_SIZE)
        ctx = QueryContext()
        loop = asyncio.get_running_loop()

        def push(event):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)

        ctx.on_event = lambda event: loop.call_soon_threadsafe(push, event)
        resources.jobs[ctx.request_id] = ctx

        async def run_query():

            async with resources.gate.enter():
                available = await resources.schema_reader.list_schemas()
                selected = available if payload.schemas_for_search == "all" else payload.schemas_for_search
                if not set(selected).issubset(available):
                    raise QueryError("unknown_schema", "Выбранная схема не существует.")
                return await resources.agent.run(payload.question, tuple(selected), ctx)

        task = asyncio.create_task(run_query())
        resources.tasks.add(task)
        try:
            yield encode_event({"event": "accepted", "request_id": ctx.request_id, "content": {}})
            while not task.done():
                if await request.is_disconnected():
                    return
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=1.0)
                    yield encode_event(event)
                except asyncio.TimeoutError:
                    yield encode_event({"event": "heartbeat", "request_id": ctx.request_id, "content": {}})
            while not queue.empty():
                yield encode_event(queue.get_nowait())
            result = await task
            value = result.result
            yield encode_event({
                "event": "result", "request_id": ctx.request_id,
                "content": {"status": result.status, "attempts": result.attempts,
                            "sql": value.sql if value else result.sql,
                            "columns": value.columns if value else [],
                            "rows": value.rows if value else [],
                            "truncated": value.truncated if value else False,
                            "error": result.error, "error_code": result.error_code},
            }, max_bytes=resources.settings.MAX_RESULT_BYTES * 2 + 65536)
        except QueryError as exc:
            yield encode_event({"event": "error", "request_id": ctx.request_id,
                                "content": {"code": exc.code, "message": str(exc)}})
        except asyncio.CancelledError:
            ctx.cancelled.set()
            raise
        except Exception:
            logger.exception("Query failed request_id=%s", ctx.request_id)
            yield encode_event({"event": "error", "request_id": ctx.request_id,
                                "content": {"code": "internal_error", "message": "Не удалось обработать запрос."}})
        finally:
            ctx.cancelled.set()
            ctx.on_event = None
            with CancelScope(shield=True):
                if not task.done():
                    task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await finish_task(task)
            resources.tasks.discard(task)
            resources.jobs.pop(ctx.request_id, None)

    return StreamingResponse(stream(), media_type="application/x-ndjson",
                              headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.post("/queries/{request_id}/cancel")
async def cancel_query(request_id: str, resources: Services = Depends(services)):
    context = resources.jobs.get(request_id)
    if context is None:
        raise HTTPException(404, "Запрос уже завершён или не существует.")
    context.cancelled.set()
    return {"status": "cancelling", "request_id": request_id}
