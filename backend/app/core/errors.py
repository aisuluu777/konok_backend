import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class DomainError(Exception):
    """Базовая бизнес-ошибка. Сервисы бросают наследников; в HTTP переводят handlers ниже."""

    status_code: int = 400
    code: str = "domain_error"

    def __init__(self, message: str = "") -> None:
        super().__init__(message)
        self.message = message


class UnauthorizedError(DomainError):
    status_code = 401
    code = "unauthorized"


class PermissionDeniedError(DomainError):
    status_code = 403
    code = "permission_denied"


class NotFoundError(DomainError):
    status_code = 404
    code = "not_found"


class ConflictError(DomainError):
    status_code = 409
    code = "conflict"


class TooManyRequestsError(DomainError):
    status_code = 429
    code = "too_many_requests"


def error_response(status_code: int, code: str, message: str, details: Any = None) -> JSONResponse:
    """Единый формат ошибок API: {"error": {"code", "message"[, "details"]}}."""
    body: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        body["details"] = jsonable_encoder(details)
    return JSONResponse(status_code=status_code, content={"error": body})


async def domain_error_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, DomainError):
        raise exc
    return error_response(exc.status_code, exc.code, exc.message)


async def http_error_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, StarletteHTTPException):
        raise exc
    return error_response(exc.status_code, "http_error", str(exc.detail))


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):
        raise exc
    return error_response(422, "validation_error", "Invalid request", exc.errors())


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # Текст исключения клиенту не отдаём — только в лог.
    logger.exception("Unhandled error", exc_info=exc)
    return error_response(500, "internal_error", "Internal server error")


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, domain_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
