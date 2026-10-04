import asyncio


async def finish_task(task: asyncio.Task, *, on_cancel=None):
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        if on_cancel is not None:
            on_cancel()
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled():
            task.exception()
        raise


async def finish_in_thread(function, *args, on_cancel=None, **kwargs):
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    return await finish_task(task, on_cancel=on_cancel)
