from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from tg_mini_app.paths import ENV_FILE


class Settings(BaseSettings):
    # Всегда читаем .env из корня проекта, а не из cwd (иначе панель «пропадает»).
    model_config = SettingsConfigDict(
        env_file=(str(ENV_FILE),),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = Field(default="local", alias="APP_ENV")
    base_url: str = Field(default="http://127.0.0.1:8000", alias="BASE_URL")
    api_host: str = Field(
        default="127.0.0.1",
        alias="API_HOST",
        description="Адрес bind для Uvicorn (0.0.0.0 — все интерфейсы).",
    )
    api_port: int = Field(default=8000, alias="API_PORT")

    database_url: str = Field(
        default="sqlite+aiosqlite:///./data/app.db",
        alias="DATABASE_URL",
    )

    bot_token: str = Field(default="", alias="BOT_TOKEN")
    operator_username: str = Field(default="@Viktor5965", alias="OPERATOR_USERNAME")
    operator_chat_id: int | None = Field(default=None, alias="OPERATOR_CHAT_ID")

    operator_panel_token: str = Field(
        default="",
        alias="OPERATOR_PANEL_TOKEN",
        description=(
            "HTTP Basic /operator-panel: логин operator, пароль = токен. "
            "Пусто — панель отключена."
        ),
    )

    courier_api_token: str = Field(
        default="",
        alias="COURIER_API_TOKEN",
        description=(
            "Секрет для API доставщика (/delivery/...?token=). "
            "Пусто — маршруты доставщика отключены."
        ),
    )

    telegram_webapp_secret: str = Field(
        default="",
        alias="TELEGRAM_WEBAPP_SECRET",
        description=(
            "HMAC-ключ для проверки initData Mini App. "
            "Пусто — используется BOT_TOKEN (как в документации Telegram)."
        ),
    )
    webapp_init_max_age_sec: int = Field(
        default=86400,
        alias="WEBAPP_INIT_MAX_AGE_SEC",
        ge=60,
        le=604800,
        description=(
            "Макс. возраст auth_date в initData (сек), защита от replay."
        ),
    )

    @field_validator("operator_chat_id", mode="before")
    @classmethod
    def _coerce_operator_chat_id(cls, value: object) -> object:
        if value is None or value == "":
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("operator_panel_token", "courier_api_token", mode="before")
    @classmethod
    def _strip_operator_panel_token(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("telegram_webapp_secret", mode="before")
    @classmethod
    def _strip_telegram_webapp_secret(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


def get_settings() -> Settings:
    return Settings()

