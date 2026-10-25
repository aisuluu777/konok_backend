"""Единый формат ошибок API: {"error": {"code": ..., "message": ..., "details": ...}}."""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

_HTTP_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "file_too_large",
    429: "too_many_requests",
}


class AppError(Exception):
    def __init__(self, status_code: int, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code or _HTTP_CODES.get(status_code, "error")


def bad_request(message: str) -> AppError:
    return AppError(400, message)


def forbidden(message: str = "Недостаточно прав") -> AppError:
    return AppError(403, message)


def not_found(message: str = "Не найдено") -> AppError:
    return AppError(404, message)


def conflict(message: str) -> AppError:
    return AppError(409, message)


def _error(status_code: int, code: str, message: str, details: Any = None) -> JSONResponse:
    body: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        body["details"] = details
    return JSONResponse({"error": body}, status_code=status_code)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error(_: Request, exc: AppError) -> JSONResponse:
        return _error(exc.status_code, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = jsonable_encoder(exc.errors(), exclude={"input", "url", "ctx"})
        return _error(422, "validation_error", "Ошибка в данных запроса", details)

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "error")
        return _error(exc.status_code, code, str(exc.detail))
