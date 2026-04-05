# Шпаргалка: локальная разработка, сервер, GitHub

Один документ с командами для **Windows (ПК)** и **Ubuntu (VPS)**. Проект: `tg_mini_app`, Python **3.12+** (в `pyproject.toml` указано `requires-python = ">=3.12"`).

**Обновить код на GitHub и на сервере с ПК одной командой:** в корне репозитория запустите **`push-and-deploy.cmd`** (нужен SSH на VPS и remote `github` или параметр `-GitRemote`). Подробно и примеры — **раздел 8.5**.

---

## 1. Python 3.12 на компьютере (Windows)

Зачем: та же версия, что на сервере (у вас был опыт с `>=3.13` в копии `pyproject.toml` — на ПК и VPS держите **одинаковый** `pyproject.toml` из репозитория).

**Вариант A — установщик с python.org**  
Скачайте Python 3.12.x для Windows, при установке отметьте **«Add python.exe to PATH»**.

**Вариант B — winget (если доступен)**

```powershell
winget install Python.Python.3.12
```

Проверка:

```powershell
py -3.12 --version
```

Дальше везде в шпаргалке для Windows используйте **`py -3.12`** или активированный venv (там уже свой `python`).

---

## 2. Виртуальная среда (venv) и зависимости

### Windows (в корне репозитория, например `C:\tg_mini_app`)

```powershell
cd C:\tg_mini_app
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Если PowerShell ругается на выполнение скриптов:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Установка пакетов:

```powershell
python -m pip install -U pip
pip install -r requirements.txt
pip install -e .
```

Проверка:

```powershell
python -c "import tg_mini_app; print('ok')"
```

**Деактивация venv:** `deactivate`

### Linux (сервер, каталог `/srv/tg_mini_app`)

```bash
cd /srv/tg_mini_app
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
pip install -e .
python -c "import tg_mini_app; print('ok')"
```

Важно: в `requirements.txt` **не должно** быть строк вроде `-e c:\tg_mini_app` с Windows-путями. Сам проект ставится **только** командой `pip install -e .` из папки проекта.

---

## 3. Файл `.env`

- Скопируйте из примера: `copy .env.example .env` (Windows) или `cp .env.example .env` (Linux).
- Заполните **`BOT_TOKEN`**, **`BASE_URL`**, **`OPERATOR_CHAT_ID`** и остальное.
- Файл **`.env` в Git не коммитится** (см. `.gitignore`).

На продакшене минимум:

```env
BASE_URL=https://vikmybot.ru
APP_ENV=production
API_HOST=127.0.0.1
API_PORT=8000
```

### 3.1. Скрипт деплоя с ПК (GitHub + сервер)

После правок кода на Windows:

```powershell
& C:\tg_mini_app\push-and-deploy.cmd -SshTarget root@77.222.35.130
```

(Путь к проекту подставьте свой.) Скрипт делает `git push`, по SSH запускает на VPS `deploy/server-update.sh` (pull, pip, перезапуск systemd). Проверка без деплоя: **`-CheckOnly`**. Свой текст коммита: **`-CommitMessage "..."`**. Полная инструкция, типичные ошибки и **`OPERATOR_CHAT_ID`** — **§8.5**.

---

## 4. Запуск API и бота

Нужны **оба** процесса: API отдаёт сайт и создаёт заказы; **бот** принимает нажатия кнопок «Да / Нет / Изменить» и оплату (polling).

### Windows — вручную (два окна PowerShell)

Окно 1 — API:

```powershell
cd C:\tg_mini_app
.\.venv\Scripts\Activate.ps1
python -m tg_mini_app.api
```

Окно 2 — бот:

```powershell
cd C:\tg_mini_app
.\.venv\Scripts\Activate.ps1
python -m tg_mini_app.bot
```

### Windows — готовые скрипты в репозитории

- `scripts\dev-start.ps1` — API + cloudflared (для отладки с временным HTTPS).
- `scripts\dev-bot.ps1` — отдельное окно с ботом.

Запуск из корня проекта:

```powershell
pwsh -File scripts\dev-start.ps1
pwsh -File scripts\dev-bot.ps1
```

Остановка всего локального: `pwsh -File scripts\dev-stop.ps1`

### Linux (VPS) — вручную

Сессия 1:

```bash
cd /srv/tg_mini_app
source .venv/bin/activate
python -m tg_mini_app.api
```

Сессия 2:

```bash
cd /srv/tg_mini_app
source .venv/bin/activate
python -m tg_mini_app.bot
```

### Linux — оба процесса в одной сессии (временно, до настройки systemd)

Удобно в **tmux** / **screen** или два SSH-окна. Пример «в фоне» из одной оболочки (логи в файлы):

```bash
cd /srv/tg_mini_app
source .venv/bin/activate
nohup python -m tg_mini_app.api >> /var/log/tg_mini_app_api.log 2>&1 &
nohup python -m tg_mini_app.bot >> /var/log/tg_mini_app_bot.log 2>&1 &
```

Остановка таких фоновых процессов:

```bash
pkill -f 'tg_mini_app.api'
pkill -f 'tg_mini_app.bot'
```

(Проверьте перед `pkill`, что не убьёте лишнее: `pgrep -af tg_mini_app`.)

---

## 5. Перезапуск (после смены `.env` или обновления кода)

1. Остановить процессы (Ctrl+C в окнах **или** `pkill` / `systemctl stop`, см. ниже).
2. При обновлении из Git: `git pull`, затем при необходимости  
   `source .venv/bin/activate` → `pip install -r requirements.txt` → `pip install -e .`
3. Снова запустить API и бота.

---

## 6. Контроль: всё ли работает

| Проверка | Команда / действие |
|----------|-------------------|
| API слушает порт | Linux: `ss -tlnp \| grep 8000` |
| HTTP локально на сервере | `curl -sS -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/health` — ожидается **200** |
| Сайт снаружи | Браузер: `https://ВАШ_ДОМЕН/webapp` |
| Процессы Python | Linux: `pgrep -af tg_mini_app` |
| Последний заказ (диагностика) | `https://ВАШ_ДОМЕН/debug/last-order` (не открывайте публично в проде без ограничений — при необходимости отключите роут в коде) |
| Nginx | `sudo systemctl status nginx` |
| SSL | `sudo certbot certificates` |

Если кнопки в Telegram «молчат» — почти всегда **не запущен бот** (`python -m tg_mini_app.bot`).

**Кнопка «Передан в доставку» после оплаты** приходит **не клиенту**, а в **чат оператора** с ботом (тот же аккаунт, что в **`OPERATOR_CHAT_ID`** в `.env`). Условия: в `.env` задан **`OPERATOR_CHAT_ID`** (числовой Telegram ID, см. `/id` у бота); оператор **хотя бы раз написал боту** `/start` (иначе Telegram может не доставить сообщение); после смены `.env` перезапустите **сервис бота** на сервере. Если кнопки нет — используйте **`/ship N`** или **`/delivery N`** или веб-панель **«Передан в доставку»**. В логах бота при ошибке доставки будет предупреждение `Handoff button not delivered…`.

---

## 7. Автозапуск на Ubuntu через systemd (рекомендуется)

Нужны **два systemd-сервиса** (API и бот). После настройки **не требуется** держать открытыми два окна SSH или «две панели» у провайдера: процессы работают в фоне, перезагрузка VPS их поднимет, если сервисы включены (`enable`).

Готовые unit-файлы лежат в репозитории:

- `deploy/systemd/tg-mini-app-api.service`
- `deploy/systemd/tg-mini-app-bot.service`

По умолчанию в них пути **`/srv/tg_mini_app`** и **`User=root`**. Если каталог проекта или пользователь другие — отредактируйте файлы **до** копирования в `/etc/systemd/system/` (или правьте уже установленные unit-файлы и снова выполните `daemon-reload`).

### 7.1. Установить unit-файлы на сервер

**Вариант A — проект уже склонирован на VPS** (удобно после `git pull`):

```bash
cd /srv/tg_mini_app
sudo cp deploy/systemd/tg-mini-app-api.service deploy/systemd/tg-mini-app-bot.service /etc/systemd/system/
```

**Вариант B — копирование с ПК (Windows, PowerShell)** подставьте пользователя Linux и хост VPS:

```powershell
scp C:\tg_mini_app\deploy\systemd\tg-mini-app-api.service USER@ВАШ_VPS:/tmp/
scp C:\tg_mini_app\deploy\systemd\tg-mini-app-bot.service USER@ВАШ_VPS:/tmp/
```

На сервере:

```bash
sudo mv /tmp/tg-mini-app-api.service /tmp/tg-mini-app-bot.service /etc/systemd/system/
```

**Вариант C — вручную:** одна SSH-сессия, `sudo nano /etc/systemd/system/tg-mini-app-api.service` (и аналогично для бота), вставьте содержимое из файлов в `deploy/systemd/` в репозитории.

### 7.2. Включить и запустить сервисы

Одна команда за другой (достаточно **одного** подключения по SSH):

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now tg-mini-app-api.service
sudo systemctl enable --now tg-mini-app-bot.service
sudo systemctl status tg-mini-app-api.service
sudo systemctl status tg-mini-app-bot.service
```

### 7.3. Логи и перезапуск

Логи:

```bash
journalctl -u tg-mini-app-api.service -f
journalctl -u tg-mini-app-bot.service -f
```

Перезапуск (после смены `.env` или обновления кода):

```bash
sudo systemctl restart tg-mini-app-api.service
sudo systemctl restart tg-mini-app-bot.service
```

### 7.4. Содержимое unit-файлов (как в репозитории)

Ниже — то же, что в `deploy/systemd/` (в т.ч. `PYTHONUNBUFFERED=1` для нормальных логов в journald).

**`/etc/systemd/system/tg-mini-app-api.service`**

```ini
[Unit]
Description=tg_mini_app FastAPI (uvicorn)
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/srv/tg_mini_app
EnvironmentFile=/srv/tg_mini_app/.env
Environment=PYTHONUNBUFFERED=1
ExecStart=/srv/tg_mini_app/.venv/bin/python -m tg_mini_app.api
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

**`/etc/systemd/system/tg-mini-app-bot.service`**

```ini
[Unit]
Description=tg_mini_app Telegram bot (aiogram)
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/srv/tg_mini_app
EnvironmentFile=/srv/tg_mini_app/.env
Environment=PYTHONUNBUFFERED=1
ExecStart=/srv/tg_mini_app/.venv/bin/python -m tg_mini_app.bot
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Для продакшена лучше завести отдельного пользователя Linux (не `root`), выставить `User=` / `Group=` в unit-файлах и выдать права на каталог проекта — сейчас шаблон упрощённый.

---

## 8. Git и GitHub

### 8.1. Регистрация репозитория на GitHub

1. На [github.com](https://github.com) создайте **новый репозиторий** (без обязательного README, если уже есть локальный проект).
2. На ПК в папке проекта:

```powershell
cd C:\tg_mini_app
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/Viktor2009/my_app.git
git push -u origin main
```

Убедитесь, что **`.env` не попал** в коммит (`git status` не должен показывать `.env`).

### 8.2. Обновление на GitHub (отправка правок с ПК)

Когда вы меняли код локально и хотите **залить изменения** в удалённый репозиторий (обычно ветка `main`):

```powershell
cd C:\tg_mini_app
git status
git add .
git commit -m "Краткое описание изменений"
```

Если на GitHub уже появились новые коммиты (другой ПК, правки на сайте GitHub), перед `push` подтяните историю и переложите свои коммиты сверху:

```powershell
git pull origin main --rebase
```

(Вместо `origin` у вас может быть remote `github` — смотрите `git remote -v`.)

Отправка на GitHub:

```powershell
git push -u origin main
```

или, если основной remote назван иначе, например `github`:

```powershell
git push -u github main
```

**Полезно помнить:**

- Снова проверьте, что **`.env`** и секреты **не** в коммите (`git status`).
- Если `push` отклонён («fetch first»), выполните `git pull … --rebase`, разрешите конфликты при необходимости (`git add …`, `git rebase --continue`), затем снова `git push`.
- После успешного `push` на **VPS** обновите код (раздел **8.4**) или одним запуском с ПК — раздел **8.5**.

### 8.3. SSH-ключ для GitHub (удобнее, чем пароль)

На ПК (PowerShell):

```powershell
ssh-keygen -t ed25519 -C "github-pc"
Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub
```

Скопируйте строку в GitHub → **Settings → SSH and GPG keys → New SSH key**.

Remote на SSH:

```powershell
git remote set-url origin git@github.com:ВАШ_ЛОГИН/ВАШ_РЕПО.git
git push
```

### 8.4. Клонирование на сервер и обновление

Первый раз на VPS:

```bash
cd /srv
git clone git@github.com:Viktor2009/my_app.git tg_mini_app
cd tg_mini_app
# создать .env на сервере вручную — не из репозитория
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

Обновление кода после правок с ПК:

```bash
cd /srv/tg_mini_app
git pull
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
sudo systemctl restart tg-mini-app-api.service tg-mini-app-bot.service
```

**Одной командой:** скрипт `deploy/server-update.sh` сам вычисляет корень репозитория (родитель каталога `deploy/`) и делает `cd` туда, поэтому **предварительный `cd` в каталог проекта не обязателен**, если указать полный путь к скрипту:

```bash
bash /srv/tg_mini_app/deploy/server-update.sh
```

Эквивалентно (если вы уже в корне репозитория):

```bash
cd /srv/tg_mini_app
bash deploy/server-update.sh
```

Если remote на GitHub называется `github`, а не `origin`:

```bash
GIT_REMOTE=github bash deploy/server-update.sh
```
==================================================
Обновление GitHub и сервера одной строкой
.\push-and-deploy.cmd -SshTarget root@77.222.35.130

Или чтобы не писать IP каждый раз (один раз в сессии PowerShell):
$env:TG_MINI_APP_DEPLOY_SSH = "root@77.222.35.130"; .\push-and-deploy.cmd
====================================================


Только обновить код и зависимости, **без** перезапуска systemd:

```bash
NO_SYSTEMD_RESTART=1 bash deploy/server-update.sh
```

Все примеры выше — **синтаксис bash** (сессия SSH на Ubuntu). В **PowerShell на Windows** префикс `ПЕРЕМЕННАЯ=значение` перед командой **не** работает: PowerShell воспринимает его как имя команды. Если запускаете тот же скрипт через **Git Bash** или **WSL** с ПК, используйте так:

```powershell
$env:GIT_REMOTE = "github"
bash deploy/server-update.sh
```

или одной строкой:

```powershell
$env:GIT_REMOTE = "github"; bash deploy/server-update.sh
```

Скрипт с `systemctl` рассчитан на **VPS**; на чистом Windows без Linux-окружения его обычно не запускают.

(Если systemd ещё не настроен — перезапустите процессы вручную.)

### 8.5. Один запуск с ПК: GitHub + сервер

Цепочка **изменили код → на GitHub → на VPS** в одном действии на **Windows (PowerShell)**:

1. Скрипт **`scripts/push-and-update-server.ps1`** делает `git push` на указанный remote (по умолчанию **`github`**, ветка **`main`**), затем по **SSH** на сервере выполняет **`deploy/server-update.sh`**.

2. **Самый простой запуск (из любой папки)** — файл в **корне репозитория** **`push-and-deploy.cmd`**: он сам переходит в каталог проекта и вызывает PowerShell с полным путём к `.ps1`.

   В **cmd** или строке «Выполнить» (Win+R):

   ```text
   C:\tg_mini_app\push-and-deploy.cmd -SshTarget root@ВАШ_IP
   ```

   В **PowerShell** (Cursor тоже):

   ```powershell
   & C:\tg_mini_app\push-and-deploy.cmd -SshTarget root@ВАШ_IP
   ```

   Либо из каталога проекта:

   ```powershell
   cd C:\tg_mini_app
   .\push-and-deploy.cmd -SshTarget root@77.222.35.130
   ```

3. Проверка окружения **без push и SSH** (диагностика):

   ```powershell
   powershell -ExecutionPolicy Bypass -File C:\tg_mini_app\scripts\push-and-update-server.ps1 -CheckOnly
   ```

   Должно быть: есть `.git`, в PATH есть `git`, remote с именем **`github`** (смотрите `git remote -v`; иначе укажите **`-GitRemote origin`**).

4. Адрес сервера можно не повторять, если задать переменную:

   ```powershell
   $env:TG_MINI_APP_DEPLOY_SSH = "root@ВАШ_IP"
   .\push-and-deploy.cmd
   ```

5. **Коммит при деплое:** если есть незакоммиченные файлы, скрипт **сам** делает `git add -A` и коммит с **стандартным сообщением** вида  
   `chore(deploy): sync 2026-04-04 15:30` (дата/время с ПК). Своё описание — параметр **`-CommitMessage "..."`** (тогда сначала коммит с ним, как раньше). Чтобы **запретить** автокоммит и получить ошибку при грязном дереве (как раньше по умолчанию): **`-RequireExplicitCommit`**.

6. Пример с **своим** сообщением коммита:

   ```powershell
   .\push-and-deploy.cmd -SshTarget root@ВАШ_IP -CommitMessage "описание правок"
   ```

7. Если на **сервере** `git pull` должен идти в remote не `origin`, а например `github`:

   ```powershell
   .\push-and-deploy.cmd -SshTarget root@IP -ServerGitRemote github
   ```

8. **Частые ошибки**

   | Симптом | Что сделать |
   |---------|-------------|
   | «Файл .ps1 не существует» | Вы не в каталоге проекта и указали относительный путь. Используйте **`push-and-deploy.cmd`** или полный путь к `.ps1`. |
   | `Remote 'github' not found` | На ПК нет remote с именем `github`. Либо `git remote add github <url>`, либо запуск с **`-GitRemote origin`**. |
   | Нужно вручную контролировать коммиты | Запуск с **`-RequireExplicitCommit`**: без чистого дерева или **`-CommitMessage`** скрипт остановится. |
   | `git push` / SSH просит пароль или отказ | Настройте **SSH-ключ** и доступ к GitHub; проверьте `ssh root@IP` с ПК. |
   | `pwsh` не распознано | Не используйте `pwsh`; вызывайте **`powershell`** или **`push-and-deploy.cmd`**. |

Условия: на ПК в PATH есть **git** и **ssh**; настроен **доступ по SSH** к VPS; на сервере есть клон и **`deploy/server-update.sh`**. Установленный отдельно **PowerShell 7** (`pwsh`) не обязателен.

---

## 9. Короткий чеклист «всё под рукой»

| Задача | Где смотреть |
|--------|----------------|
| Запуск локально | Разделы 2 и 4 (Windows) |
| Запуск на VPS | Разделы 2, 4, 7 |
| Почему не работают кнопки в боте | Раздел 6 + оба процесса из раздела 4 |
| Залить правки на GitHub (ПК) | Раздел 8.2 |
| Обновить код на сервере | Раздел 8.4, `deploy/server-update.sh` |
| ПК → GitHub → сервер одним шагом | Раздел 8.5, `push-and-deploy.cmd`, `scripts/push-and-update-server.ps1` |
| Домен и HTTPS | nginx + certbot (ваш хостинг); `BASE_URL` в `.env` |

---

## 10. Если что-то пошло не так

1. Прочитайте текст ошибки в терминале или `journalctl -u ...`.
2. Проверьте `pgrep -af tg_mini_app` на сервере.
3. Проверьте `curl http://127.0.0.1:8000/health` на VPS.
4. Убедитесь, что **один** `pyproject.toml` с **`requires-python = ">=3.12"`** и на ПК, и на сервере.

Этот файл можно дополнять своими заметками (IP сервера, имена systemd-сервисов, ссылки на панель хостинга).
