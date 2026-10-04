from fastapi import Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)


async def handle_app_error(request: Request, error: AppError) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": error.code, "message": error.message}}, status_code=error.status
    )
