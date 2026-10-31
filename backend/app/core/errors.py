from enum import Enum
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class ErrorCode(str, Enum):
    JOB_IN_PROGRESS = "JOB_IN_PROGRESS"
    STALE_VERSION = "STALE_VERSION"
    LLM_INVALID_OUTPUT = "LLM_INVALID_OUTPUT"
    REPO_UNREACHABLE = "REPO_UNREACHABLE"
    TEMPLATE_INVALID = "TEMPLATE_INVALID"
    TOOL_MISSING = "TOOL_MISSING"
    NOT_FOUND = "NOT_FOUND"


ERROR_STATUS_MAP: dict[ErrorCode, int] = {
    ErrorCode.JOB_IN_PROGRESS: 409,
    ErrorCode.STALE_VERSION: 409,
    ErrorCode.LLM_INVALID_OUTPUT: 502,
    ErrorCode.REPO_UNREACHABLE: 422,
    ErrorCode.TEMPLATE_INVALID: 400,
    ErrorCode.TOOL_MISSING: 503,
    ErrorCode.NOT_FOUND: 404,
}


class ApiError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        detail: dict[str, Any] | None = None,
        status_code: int | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail
        self.status_code = (
            status_code if status_code is not None else ERROR_STATUS_MAP.get(code, 500)
        )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
        code_str = exc.code.value if isinstance(exc.code, ErrorCode) else str(exc.code)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": code_str,
                "message": exc.message,
                "detail": exc.detail,
            },
        )

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        import traceback
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={
                "code": "INTERNAL_SERVER_ERROR",
                "message": str(exc) or "An internal server error occurred",
                "detail": {"type": type(exc).__name__},
            },
        )
