# tg_mini_app: Telegram Mini App для заказа суши

Коротко о репозитории: **FastAPI** (сайт и API), **aiogram 3** (бот), **SQLite** через SQLAlchemy/aiosqlite. Витрина — HTML (**Jinja2**) и статика, основной клиентский скрипт — **`app_v3.js`**. В каждом блоке ниже — **один понятный способ** для типичной задачи на Windows; сервер и деплой — в других файлах документации.

---

## Этап 1. Требования

- **Python 3.12+** ---> как задано в **`pyproject.toml`** (`requires-python = ">=3.12"`).

---

## Этап 2. Виртуальная среда и пакеты

Все команды — **в корне репозитория** (например `C:\tg_mini_app`), в **PowerShell**, после активации venv.

Один способ установки зависимостей:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -r requirements.txt
pip install -e .
```

- `pip install -r requirements.txt` ---> внешние библиотеки.
- `pip install -e .` ---> установить сам пакет **`tg_mini_app`** из текущей папки в режиме разработки.

Если PowerShell запрещает скрипты активации — один раз для пользователя:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

---

## Этап 3. Файл `.env`

- `copy .env.example .env` (в корне проекта) ---> создать **`.env`** из примера.

Дальше заполните **`BOT_TOKEN`**, **`BASE_URL`**, при необходимости **`OPERATOR_CHAT_ID`**, токены панелей — подсказки в **`.env.example`** в корне репозитория. Секреты в Git не коммитятся (см. **`.gitignore`**).

---

## Этап 4. Запуск на ПК (два процесса)

Нужны **оба** процесса: API отдаёт сайт и эндпоинты, бот обрабатывает нажатия и сценарии в Telegram (**polling**).

**Окно 1 — API:**

```powershell
python -m tg_mini_app.api
```

**Окно 2 — бот:**

```powershell
python -m tg_mini_app.bot
```

Остановка в каждом окне: **Ctrl+C**.

- Локально по умолчанию сайт открывается как **`http://127.0.0.1:8000`** — смотрите **`API_HOST`** и **`API_PORT`** в **`.env`**.
- **`APP_ENV=local`** ---> для отладки в обычном браузере можно передать **`customer_tg_id`** без подписанного **initData** Mini App (на проде обычно **`production`** и проверка initData).

---

## Этап 5. Основные адреса после запуска API

Все пути ниже — **от корня сайта** (тот же хост и порт, что у API).

- **`/webapp`** ---> Mini App: каталог, корзина, оформление заказа.
- **`/operator-panel`** ---> панель оператора (заказы), если в **`.env`** задан непустой **`OPERATOR_PANEL_TOKEN`** (иначе **404**). Вход: **`/operator-panel/login`** (cookie-сессия) или HTTP Basic: логин **`operator`**, пароль = токен.
- **`/operator-panel/catalog-manage`** ---> HTML-редактор категорий и товаров, загрузка файлов фото (медиа — **`/catalog-media/...`**). Тот же доступ, что у панели оператора.
- **`/operator-panel/catalog/...`** ---> JSON API CRUD каталога для программного доступа (тот же токен / сессия).
- **`/delivery/login`** ---> панель курьера, если задан **`COURIER_API_TOKEN`**.
- **`/health`** ---> короткий JSON-ответ, что API жив (проверка работоспособности).

Подробности по переменным и продакшену — в **`docs/SERVER_RUNBOOK.md`**.

---

## Что дальше

- **`docs/PLAN.md`** ---> ТЗ, фактическое состояние кода и дорожная карта.
- **`docs/SERVER_RUNBOOK.md`** ---> venv на сервере, **systemd**, обновление кода, бэкапы, **`push-and-deploy.cmd`** / **`deploy/server-update.sh`**.
- **`docs/LINUX_SERVER_BEGINNER.md`** ---> SSH, nginx, **journalctl**, **curl**, **ufw** с точки зрения начинающего.
- **`docs/ARCHITECTURE_MODULES.md`** ---> как устроены модули и роутеры.
- Опционально на Windows: **`scripts/dev-start.ps1`**, **`scripts/dev-bot.ps1`** — если нужен туннель/отладка поверх обычного запуска.
