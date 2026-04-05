# Шпаргалка: локальная разработка, сервер, GitHub

Один документ с командами для **Windows (ПК)** и **Ubuntu (VPS)**. Проект **`tg_mini_app`**, Python **3.12+** (в `pyproject.toml`: `requires-python = ">=3.12"`).

В каждом разделе — **один понятный способ** там, где это уместно; длинные таблицы заменены на формат **`команда ---> что делает`**.

**Быстрый путь «с ПК на GitHub и на сервер»:** в корне репозитория запустите **`push-and-deploy.cmd`** (нужны SSH на VPS и remote **`github`** или параметр **`-GitRemote`**). Подробности — в разделе **8.5**.

---

## 1. Python 3.12 на компьютере (Windows)

Зачем: на ПК и на сервере должна быть **согласованная** версия Python; не редактируйте `requires-python` в `pyproject.toml` «наугад» под одну машину.

**Один способ установки:** скачайте установщик **Python 3.12.x** с [python.org](https://www.python.org/downloads/) для Windows и при установке включите **«Add python.exe to PATH»**.

Проверка в **PowerShell**:

```powershell
py -3.12 --version
```

Дальше для Windows в этой шпаргалке используйте **`py -3.12`** или **`python`** из **активированного** venv (там уже свой интерпретатор).

---

## 2. Виртуальная среда (venv) и зависимости

### Windows (корень репозитория, например `C:\tg_mini_app`)

```powershell
cd C:\tg_mini_app
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Если PowerShell запрещает скрипты:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Установка зависимостей:

- `python -m pip install -U pip` ---> обновить **pip** внутри venv.
- `pip install -r requirements.txt` ---> поставить зависимости из файла.
- `pip install -e .` ---> установить сам проект в **режиме разработки** из текущей папки.

Проверка:

```powershell
python -c "import tg_mini_app; print('ok')"
```

**Выйти из venv:** `deactivate`

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

Важно: в **`requirements.txt`** не должно быть строк с **Windows-путями** вроде `-e c:\tg_mini_app`. Сам проект ставится **только** командой **`pip install -e .`** из папки проекта.

---

## 3. Файл `.env`

- `copy .env.example .env` (Windows, в корне проекта) или `cp .env.example .env` (Linux) ---> создать **`.env`** из примера.
- Заполните **`BOT_TOKEN`**, **`BASE_URL`**, **`OPERATOR_CHAT_ID`** и остальное по комментариям в **`.env.example`**.
- Файл **`.env` в Git не коммитится** (см. **`.gitignore`**).

Минимум для продакшена (пример значений):

```env
BASE_URL=https://vikmybot.ru
APP_ENV=production
API_HOST=127.0.0.1
API_PORT=8000
```

### Деплой с ПК одной командой (кратко)

После правок кода на Windows (подставьте свой путь и пользователь@сервер):

```powershell
& C:\tg_mini_app\push-and-deploy.cmd -SshTarget root@77.222.35.130
```

Скрипт делает **`git push`**, по SSH на VPS запускает **`deploy/server-update.sh`** (pull, pip, перезапуск **systemd**). Без деплоя, только проверка: **`-CheckOnly`**. Свой текст коммита: **`-CommitMessage "..."`**. Полная инструкция — раздел **8.5**.

---

## 4. Запуск API и бота

Нужны **оба** процесса: **API** отдаёт сайт и заказы; **бот** обрабатывает кнопки и оплату (**polling**).

### Windows — два окна PowerShell (основной способ)

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

Остановка: **Ctrl+C** в каждом окне.

Дополнительно в репозитории есть **`scripts\dev-start.ps1`** (API + временный HTTPS через cloudflared) и **`scripts\dev-bot.ps1`** — если разберётесь с ними, ими можно заменить ручной запуск под отладку.

### Linux на VPS — в продакшене через systemd

Основной способ на сервере — **раздел 7** (**`tg-mini-app-api.service`** и **`tg-mini-app-bot.service`**). Ручной запуск в двух SSH-сессиях — только для отладки до настройки служб:

```bash
cd /srv/tg_mini_app
source .venv/bin/activate
python -m tg_mini_app.api
```

и во второй сессии то же с **`python -m tg_mini_app.bot`**.

Если службы **systemd** уже запущены, **не** держите параллельно ручной запуск — см. раздел **5** (конфликт порта **8000**).

---

## 5. Перезапуск после смены `.env` или кода

### На VPS, если API и бот уже в **systemd** (раздел 7)

1. Если меняли только **`.env`**: достаточно перезапуска служб (новые переменные подхватятся при старте процесса):

```bash
sudo systemctl restart tg-mini-app-api.service
sudo systemctl restart tg-mini-app-bot.service
```

2. Если обновляли **код с Git** на сервере: сначала подтянуть репозиторий и зависимости, **потом** перезапуск:

```bash
cd /srv/tg_mini_app
git pull
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
sudo systemctl restart tg-mini-app-api.service tg-mini-app-bot.service
```

(Путь к проекту подставьте свой, если не **`/srv/tg_mini_app`**. Либо один раз **`bash deploy/server-update.sh`** — раздел **8.4**.)

3. **Не запускайте второй раз** **`python -m tg_mini_app.api`** (и бота) вручную в SSH, пока эти же процессы уже крутятся в **systemd**: порт **8000** будет занят, в логах Uvicorn появится **`address already in use`**. Для отладки вручную сначала остановите службу: **`sudo systemctl stop tg-mini-app-api.service`** (и при необходимости бота), потом запускайте из venv; после отладки — **`sudo systemctl start …`** или **`restart`**.

### На ПК (локально без systemd)

Остановка: **Ctrl+C** в окнах с API и ботом. После смены кода или **`.env`** — снова запустить оба процесса (раздел **4**).

---

## 6. Контроль: всё ли работает

- `ss -tlnp | grep 8000` (Linux на сервере) ---> убедиться, что **что-то слушает порт 8000** (обычно API).
- `curl -sS -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/health` (на VPS) ---> ожидается **`200`**.
- Открыть в браузере **`https://ВАШ_ДОМЕН/webapp`** ---> проверка сайта **снаружи**.
- `pgrep -af tg_mini_app` (Linux) ---> список процессов Python с именем проекта.
- **`https://ВАШ_ДОМЕН/debug/last-order`** — диагностика последнего заказа; **не** открывайте публично в проде без ограничений (при необходимости отключите роут в коде).
- `sudo systemctl status nginx` ---> состояние **nginx**.
- `sudo certbot certificates` ---> сведения о **SSL-сертификатах** Let’s Encrypt.

Если кнопки в Telegram «молчат» — чаще всего **не запущен бот** (**`python -m tg_mini_app.bot`** или служба **`tg-mini-app-bot.service`**).

**Кнопка «Передан в доставку» после оплаты** уходит **не клиенту**, а в **чат оператора** (тот же бот, **`OPERATOR_CHAT_ID`** в **`.env`**). Нужно: в **`.env`** задан числовой **`OPERATOR_CHAT_ID`**; оператор **хотя бы раз** написал боту **`/start`**; после смены **`.env`** — перезапуск **службы бота**. Если кнопки нет — команды **`/ship N`**, **`/delivery N`** или веб-панель **«Передан в доставку»**. В логах бота при сбое доставки может быть предупреждение `Handoff button not delivered…`.

---

## 7. Автозапуск на Ubuntu через systemd

Нужны **два unit-файла**: API и бот. После настройки процессы работают в фоне и поднимаются после перезагрузки VPS (**`enable`**).

Готовые файлы в репозитории:

- `deploy/systemd/tg-mini-app-api.service`
- `deploy/systemd/tg-mini-app-bot.service`

По умолчанию пути **`/srv/tg_mini_app`** и **`User=root`**. Если у вас другой каталог или пользователь — отредактируйте файлы **в репозитории** или уже в **`/etc/systemd/system/`**, затем снова **`daemon-reload`**.

### Установить unit-файлы на сервер

**Один способ** (проект уже лежит на VPS, вы вошли по SSH):

```bash
cd /srv/tg_mini_app
sudo cp deploy/systemd/tg-mini-app-api.service deploy/systemd/tg-mini-app-bot.service /etc/systemd/system/
```

Если файлы нужно занести **с Windows**, один раз скопируйте их **scp** в **`/tmp/`** на сервере, затем **`sudo mv /tmp/tg-mini-app-*.service /etc/systemd/system/`** (пути к файлам на ПК — как в **`LINUX_SERVER_BEGINNER.md`**, этап 3).

### Включить и запустить

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now tg-mini-app-api.service
sudo systemctl enable --now tg-mini-app-bot.service
sudo systemctl status tg-mini-app-api.service
sudo systemctl status tg-mini-app-bot.service
```

### Логи и перезапуск

- `sudo journalctl -u tg-mini-app-api.service -f` ---> поток логов API (выход — **Ctrl+C**).
- `sudo journalctl -u tg-mini-app-bot.service -f` ---> поток логов бота.

После смены **`.env`** или обновления кода:

```bash
sudo systemctl restart tg-mini-app-api.service
sudo systemctl restart tg-mini-app-bot.service
```

Полный порядок при обновлении кода на сервере (**`git pull`**, **`pip`**, рестарт без дублирования ручного запуска) — в разделе **5**.

### Содержимое unit-файлов (как в репозитории)

Ниже то же, что в **`deploy/systemd/`** (в т.ч. **`PYTHONUNBUFFERED=1`** для нормальных логов в **journald**).

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

Для продакшена лучше отдельный пользователь Linux (не **`root`**), **`User=`** / **`Group=`** в unit-файлах и права на каталог проекта — шаблон сейчас упрощённый.

---

## 8. Git и GitHub

### 8.1. Первый раз: репозиторий на GitHub

1. На [github.com](https://github.com) создайте **новый репозиторий** (можно без README, если проект уже локальный).
2. На ПК в папке проекта:

```powershell
cd C:\tg_mini_app
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/ВАШ_ЛОГИН/ВАШ_РЕПО.git
git push -u origin main
```

Убедитесь, что **`.env` не в коммите** — **`git status`** не должен показывать **`.env`** среди готовых к коммиту файлов.

### 8.2. Отправить правки на GitHub

```powershell
cd C:\tg_mini_app
git status
git add .
git commit -m "Краткое описание изменений"
```

Если на GitHub уже есть новые коммиты, перед **`push`** подтяните историю:

```powershell
git pull origin main --rebase
```

(Вместо **`origin`** у вас может быть remote **`github`** — смотрите **`git remote -v`**.)

Отправка:

```powershell
git push -u origin main
```

или, если основной remote назван **`github`**:

```powershell
git push -u github main
```

Полезно помнить:

- Снова проверьте, что **`.env`** и секреты **не** попали в коммит.
- Если **`push`** отклонён («fetch first») — **`git pull … --rebase`**, при конфликтах **`git add …`**, **`git rebase --continue`**, затем снова **`git push`**.
- После успешного **`push`** обновите код на VPS (раздел **8.4**) или запустите **`push-and-deploy.cmd`** (раздел **8.5**).

### 8.3. SSH-ключ для GitHub

На ПК в PowerShell:

```powershell
ssh-keygen -t ed25519 -C "github-pc"
Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub
```

Скопируйте вывод в GitHub: **Settings → SSH and GPG keys → New SSH key**.

Переключить remote на SSH:

```powershell
git remote set-url origin git@github.com:ВАШ_ЛОГИН/ВАШ_РЕПО.git
git push
```

### 8.4. Клонирование на сервер и обновление

**Первый раз на VPS:**

```bash
cd /srv
git clone git@github.com:ВАШ_ЛОГИН/ВАШ_РЕПО.git tg_mini_app
cd tg_mini_app
```

Создайте **`.env` на сервере вручную** (не из репозитория). Затем venv и пакеты:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

**Обновление после правок с ПК:**

```bash
cd /srv/tg_mini_app
git pull
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
sudo systemctl restart tg-mini-app-api.service tg-mini-app-bot.service
```

**Один скрипт на сервере** (pull, зависимости, по умолчанию перезапуск **systemd**):

```bash
bash /srv/tg_mini_app/deploy/server-update.sh
```

Если вы уже в корне репозитория, то же самое:

```bash
cd /srv/tg_mini_app
bash deploy/server-update.sh
```

Если на сервере remote называется **`github`**, а не **`origin`**:

```bash
GIT_REMOTE=github bash /srv/tg_mini_app/deploy/server-update.sh
```

Только обновить код и зависимости **без** перезапуска **systemd**:

```bash
NO_SYSTEMD_RESTART=1 bash /srv/tg_mini_app/deploy/server-update.sh
```

Префикс **`ПЕРЕМЕННАЯ=значение`** перед командой — синтаксис **bash** на сервере. В **PowerShell на Windows** так не пишут; если запускаете скрипт через **Git Bash** / **WSL** с ПК:

```powershell
$env:GIT_REMOTE = "github"
bash deploy/server-update.sh
```

Скрипт с **`systemctl`** рассчитан на **VPS**. Если **systemd** ещё не настроен — перезапустите процессы вручную (раздел 4).

### 8.5. Один запуск с ПК: GitHub + сервер

Скрипт **`scripts/push-and-update-server.ps1`** делает **`git push`** на указанный remote (по умолчанию **`github`**, ветка **`main`**), затем по **SSH** на сервере выполняет **`deploy/server-update.sh`**.

**Удобная обёртка** — **`push-and-deploy.cmd`** в корне репозитория: сам переходит в каталог проекта и вызывает PowerShell с полным путём к **`.ps1`**.

В **cmd** или **Win+R**:

```text
C:\tg_mini_app\push-and-deploy.cmd -SshTarget root@ВАШ_IP
```

В **PowerShell**:

```powershell
& C:\tg_mini_app\push-and-deploy.cmd -SshTarget root@ВАШ_IP
```

Из каталога проекта:

```powershell
cd C:\tg_mini_app
.\push-and-deploy.cmd -SshTarget root@77.222.35.130
```

Проверка окружения **без** push и SSH:

```powershell
powershell -ExecutionPolicy Bypass -File C:\tg_mini_app\scripts\push-and-update-server.ps1 -CheckOnly
```

Должно быть: есть **`.git`**, в PATH есть **`git`**, remote с именем **`github`** (иначе **`-GitRemote origin`**).

Чтобы не указывать сервер каждый раз:

```powershell
$env:TG_MINI_APP_DEPLOY_SSH = "root@ВАШ_IP"
.\push-and-deploy.cmd
```

**Автокоммит:** если есть незакоммиченные файлы, скрипт может сделать **`git add -A`** и коммит с сообщением вида **`chore(deploy): sync …`**. Свой текст: **`-CommitMessage "..."`**. Чтобы **запретить** автокоммит и получить ошибку при «грязном» дереве: **`-RequireExplicitCommit`**.

Пример с своим сообщением:

```powershell
.\push-and-deploy.cmd -SshTarget root@ВАШ_IP -CommitMessage "описание правок"
```

Если на **сервере** для **`git pull`** нужен remote **`github`**:

```powershell
.\push-and-deploy.cmd -SshTarget root@ВАШ_IP -ServerGitRemote github
```

**Частые ошибки:**

- Сообщение «файл **`.ps1`** не существует» ---> вы не в каталоге проекта и указали относительный путь; используйте **`push-and-deploy.cmd`** или **полный путь** к скрипту.
- **`Remote 'github' not found`** ---> на ПК нет remote **`github`**; выполните **`git remote add github <url>`** или запуск с **`-GitRemote origin`**.
- Нужен ручной контроль коммитов ---> **`-RequireExplicitCommit`**.
- **`git push`** или SSH просит пароль / отказ ---> настройте **SSH-ключ** и доступ к GitHub; проверьте **`ssh root@IP`** с ПК.
- **`pwsh` не распознано** ---> вызывайте **`powershell`** или **`push-and-deploy.cmd`**, не **`pwsh`**.

Условия: на ПК в PATH есть **`git`** и **`ssh`**; настроен вход по **SSH** на VPS; на сервере есть клон и **`deploy/server-update.sh`**. Отдельный **PowerShell 7** (**`pwsh`**) не обязателен.

---

## 9. Короткий чеклист

- **Запуск локально (Windows)** — разделы **2** и **4**.
- **Запуск на VPS** — разделы **2**, **4**, **7**.
- **Кнопки в боте не работают** — раздел **6** и оба процесса из раздела **4** / службы из раздела **7**.
- **Залить правки на GitHub** — раздел **8.2**.
- **Обновить код на сервере** — раздел **8.4**, скрипт **`deploy/server-update.sh`**.
- **ПК → GitHub → сервер одним шагом** — раздел **8.5**, **`push-and-deploy.cmd`**.
- **Домен и HTTPS** — nginx и certbot у провайдера; **`BASE_URL`** в **`.env`**; детали по серверу — **`LINUX_SERVER_BEGINNER.md`** (nginx, curl, ufw).

---

## 10. Если что-то пошло не так

1. Прочитайте текст ошибки в терминале или **`sudo journalctl -u tg-mini-app-api.service`** / **`-u tg-mini-app-bot.service`**.
2. **`pgrep -af tg_mini_app`** на сервере — какие процессы запущены.
3. **`curl -sS http://127.0.0.1:8000/health`** на VPS — отвечает ли API.
4. Один общий **`pyproject.toml`** с **`requires-python = ">=3.12"`** на ПК и на сервере.

Дополняйте файл своими заметками (IP сервера, имена служб, ссылки на панель хостинга).
