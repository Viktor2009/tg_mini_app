"""Абсолютные пути к ресурсам пакета (не зависят от cwd при запуске)."""

from __future__ import annotations

from pathlib import Path

PACKAGE_ROOT: Path = Path(__file__).resolve().parent
# Репозиторий: .../tg_mini_app (рядом лежат .env, data/, pyproject.toml).
PROJECT_ROOT: Path = PACKAGE_ROOT.parent.parent
TEMPLATES_DIR: Path = PACKAGE_ROOT / "templates"
STATIC_DIR: Path = PACKAGE_ROOT / "static"
# Загрузки фото для каталога (в .gitignore вместе с data/).
CATALOG_UPLOADS_DIR: Path = PROJECT_ROOT / "data" / "catalog_uploads"
ENV_FILE: Path = PROJECT_ROOT / ".env"
