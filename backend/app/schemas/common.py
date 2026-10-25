from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Language = Literal["ky", "ru", "en"]


class Schema(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Photo(Schema):
    id: int
    url: str


class Message(BaseModel):
    message: str


class ErrorBody(BaseModel):
    code: str = Field(
        description="bad_request, unauthorized, forbidden, not_found, conflict, "
        "validation_error, too_many_requests, email_not_confirmed, user_blocked, file_too_large"
    )
    message: str = Field(description="Текст для человека (на русском)")
    details: list[dict[str, Any]] | None = Field(
        default=None, description="Только у validation_error: какие поля неверны"
    )


class ErrorOut(BaseModel):
    """Единый формат всех ошибок API."""

    error: ErrorBody


_ERROR_CODES = {
    400: "Неверный запрос (бизнес-правило)",
    401: "Нет токена, токен недействителен или пользователь заблокирован",
    403: "Нет прав на действие",
    404: "Не найдено (или чужой ресурс)",
    409: "Конфликт с текущим состоянием (нет мест, бронь уже отменена …)",
    422: "Ошибка в данных запроса",
    429: "Слишком много попыток",
}
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    code: {"model": ErrorOut, "description": text} for code, text in _ERROR_CODES.items()
}
