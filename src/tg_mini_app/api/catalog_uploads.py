"""Сохранение загруженных изображений каталога на диск."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import UploadFile

MAX_IMAGE_BYTES = 5 * 1024 * 1024

_ALLOWED_CT: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

_EXT_FALLBACK = {".jpg": ".jpg", ".jpeg": ".jpg", ".png": ".png", ".webp": ".webp", ".gif": ".gif"}


def ensure_catalog_uploads_dir(base: Path) -> None:
    base.mkdir(parents=True, exist_ok=True)


def save_catalog_image(upload: UploadFile, dest_dir: Path) -> str:
    """
    Сохраняет файл, возвращает имя для URL (только basename, без путей).

    Raises:
        ValueError: тип или размер не подходят.
    """
    ct = (upload.content_type or "").split(";")[0].strip().lower()
    ext = _ALLOWED_CT.get(ct)
    if ext is None and upload.filename:
        suf = Path(upload.filename).suffix.lower()
        ext = _EXT_FALLBACK.get(suf)
    if ext is None:
        msg = f"Допустимы фото: JPEG, PNG, WebP, GIF (получено: {ct or 'неизвестно'})"
        raise ValueError(msg)

    name = f"{uuid.uuid4().hex}{ext}"
    path = dest_dir / name
    size = 0
    with path.open("xb") as f:
        while True:
            chunk = upload.file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_IMAGE_BYTES:
                path.unlink(missing_ok=True)
                raise ValueError(f"Файл больше {MAX_IMAGE_BYTES // (1024 * 1024)} МБ")
            f.write(chunk)
    return name
