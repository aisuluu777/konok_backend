from typing import Annotated

from fastapi import APIRouter, Depends, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.core import storage
from app.core.errors import NotFoundError
from app.core.security import CurrentUser, get_current_user

router = APIRouter(tags=["files"])


class UploadOut(BaseModel):
    url: str


@router.post("/uploads", status_code=status.HTTP_201_CREATED)
async def upload(
    file: UploadFile, _: Annotated[CurrentUser, Depends(get_current_user)]
) -> UploadOut:
    """Публичный файл (фото, видео-визитка R-07); ссылку потом кладут в профиль/место."""
    name = await storage.save(file.file, "public")
    return UploadOut(url=f"/files/{name}")


@router.get("/files/{name:path}")  # :path — чтобы и "../.." доходило до проверки имени
async def get_file(name: str) -> FileResponse:
    found = storage.resolve(name, "public")
    if found is None:
        raise NotFoundError("File not found")
    path, media_type = found
    # nosniff: браузер не станет «угадывать» HTML в пользовательском файле.
    return FileResponse(path, media_type=media_type, headers={"X-Content-Type-Options": "nosniff"})
