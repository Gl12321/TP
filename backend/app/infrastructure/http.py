from fastapi.responses import JSONResponse


class BodyLimitMiddleware:
    def __init__(self, app, max_bytes=2097152):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in {"GET", "HEAD", "OPTIONS"}:
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            await JSONResponse(
                {"error": {"code": "invalid_length", "message": "Некорректная длина запроса"}},
                status_code=400,
            )(scope, receive, send)
            return
        if declared > self.max_bytes or declared < 0:
            await self.reject(scope, receive, send)
            return
        chunks = []
        length = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            length += len(body)
            if length > self.max_bytes:
                await self.reject(scope, receive, send)
                return
            chunks.append(body)
            if not message.get("more_body", False):
                break
        replayed = False

        async def replay():
            nonlocal replayed
            if replayed:
                return await receive()
            replayed = True
            return {"type": "http.request", "body": b"".join(chunks), "more_body": False}

        await self.app(scope, replay, send)

    async def reject(self, scope, receive, send):
        await JSONResponse(
            {"error": {"code": "body_too_large", "message": "Запрос превышает допустимый размер"}},
            status_code=413,
        )(scope, receive, send)
