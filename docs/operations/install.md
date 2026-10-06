# Установка с нуля

Это путь от компьютера без окружения проекта до работающего интерфейса. Сначала устанавливаются Python и Docker, затем проверяется их готовность, после чего одна команда собирает и запускает приложение. PostgreSQL, Node.js и зависимости API/агента для этого способа устанавливаются внутри контейнеров.

Все команды проекта выполняйте в корне репозитория, где лежит `run.py`. Сам репозиторий должен быть уже скачан или клонирован. Установка инструментов требует сети; первый запуск дополнительно получает образы и выбранные модели. Существующие аккаунты, ключи и базы при повторном запуске сохраняются.

Без установленного Git откройте [репозиторий TP](https://github.com/Gl12321/TP), выберите `Code → Download ZIP`, распакуйте архив и откройте терминал в папке с `run.py`. Для запуска Git не нужен.

## Выберите способ

| Цель | Что поставить на компьютер | Дальше |
| --- | --- | --- |
| Полное приложение с ассистентом | Python 3.11+ и Docker с Linux-контейнерами | Раздел Windows или Linux ниже |
| Обычный интерфейс без загрузки моделей | Те же Python и Docker | `run.py --without-ai`; пространство будет пустым |
| Готовая учебная сеть с ролями и цифрами | Python, зависимости API, Node.js 24 для сборки, отдельный PostgreSQL 16 | [Demo с нуля](../demo/README.md#подготовка-demo-с-нуля) |
| Изменение кода и автоматические проверки | Python, Node.js и зависимости разработки | [Разработка](../development/README.md) |

Модель и PostgreSQL не заменяют установленный Python; установленный Docker CLI не заменяет запущенный Docker Engine. Команда `python`, открывающая Microsoft Store, не является рабочим интерпретатором.

## Windows 11: Python, WSL 2 и Docker Desktop

Нужны 64-битная Windows, включённая виртуализация процессора и поддерживаемая Docker Desktop версия ОС. Если установщик сообщает о виртуализации, включите Intel VT-x/AMD-V в BIOS/UEFI. Актуальные системные требования — в [официальной инструкции Docker](https://docs.docker.com/desktop/setup/install/windows-install/).

### 1. Установите Python

Откройте PowerShell и выполните:

```powershell
winget install --exact --id Python.Python.3.12 --source winget --accept-source-agreements --accept-package-agreements
```

Закройте и заново откройте терминал, чтобы он получил обновлённый PATH. Проверьте:

```powershell
py -3.12 --version
```

Ожидается `Python 3.12.x`. Ниже используется именно `py -3.12`, чтобы не зависеть от заглушки `python.exe`. Если `winget` отсутствует, установите/обновите «Установщик приложений» Microsoft, либо используйте [официальный установщик Python](https://www.python.org/downloads/windows/) с Python Launcher. [Описание launcher](https://docs.python.org/3.12/using/windows.html#python-launcher-for-windows).

### 2. Подготовьте WSL 2

Откройте PowerShell **от имени администратора**:

```powershell
wsl --install --no-distribution
```

После успешного завершения перезагрузите компьютер, если этого требует Windows. Затем выполните:

```powershell
wsl --update
wsl --version
```

Для Docker нужен WSL 2; отдельную Ubuntu для проекта устанавливать не требуется. Если WSL уже установлен и подходит Docker, повторять установку не нужно. [Установка WSL](https://learn.microsoft.com/en-us/windows/wsl/install), [значение флагов](https://learn.microsoft.com/en-us/windows/wsl/basic-commands).

### 3. Установите и запустите Docker Desktop

В PowerShell:

```powershell
winget install --exact --id Docker.DockerDesktop --source winget --accept-source-agreements --accept-package-agreements
```

Установщик может запросить повышение прав. После установки откройте **Docker Desktop из меню «Пуск»**, завершите первоначальную настройку и дождитесь работающего Engine. Используйте WSL 2 backend и Linux-контейнеры. Если предложен пункт `Switch to Linux containers`, выберите его. Docker Desktop после установки нужно запустить отдельно.

Заново откройте терминал и проверьте:

```powershell
docker compose version
docker info --format '{{.OSType}}'
```

Первая команда должна показать версию Compose; вторая — `linux`. Ошибка соединения с Engine означает, что Docker Desktop ещё не запущен или не закончил инициализацию. Поле `linux` обязательно: образы проекта основаны на Linux.

### 4. Проверьте проект и запустите

В корне скачанного проекта:

```powershell
py -3.12 run.py --check
py -3.12 run.py --model qwen3.5-9b
```

Проверка `--check` читает состояние Python, Compose и Engine, завершается кодом `0` при готовности и ничего не устанавливает. Если она завершилась ошибкой, устраните указанную причину до запуска.

Откройте `http://localhost:8000`, когда launcher выведет адрес. Код первоначальной настройки появится в терминале; он нужен для создания первого владельца. Подготовка ассистента продолжается до сообщения о полной готовности.

Для первого знакомства без моделей:

```powershell
py -3.12 run.py --without-ai
```

Docker и сборка образов для этой команды всё равно нужны. Она открывает новое пустое приложение, а не заполненное demo. Остановка:

```powershell
py -3.12 run.py --stop
```

## Linux: Ubuntu 24.04 LTS

Команды ниже предназначены для новой Ubuntu 24.04 с `sudo` и systemd. Для другого дистрибутива используйте его пакетный менеджер и [официальную инструкцию Docker Engine](https://docs.docker.com/engine/install/); одинаковыми остаются команды `run.py` после установки Python и Docker. Уже установленный Docker не переустанавливайте этим рецептом.

### 1. Установите Python и Docker из официального репозитория

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv ca-certificates curl
sudo install -d -m 0755 /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
razbor_arch=$(dpkg --print-architecture)
razbor_codename=$(. /etc/os-release && printf '%s' "$VERSION_CODENAME")
printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu %s stable\n' "$razbor_arch" "$razbor_codename" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
```

`sudo` запрашивает пароль вашей системной учётной записи. После добавления в группу `docker` **выйдите из пользовательской сессии и войдите снова**; для SSH переподключитесь. Это даёт текущему пользователю доступ к Engine без запуска проекта от root. Основа рецепта — [официальная установка Docker на Ubuntu](https://docs.docker.com/engine/install/ubuntu/).

### 2. Проверьте и запустите

```bash
python3 --version
docker compose version
docker info --format '{{.OSType}}'
python3 run.py --check
python3 run.py --model qwen3.5-9b
```

Ожидаются Python 3.11+, доступный Compose и `linux` от Engine. `permission denied` у Docker обычно означает, что сессия ещё не получила новую группу. После вывода адреса откройте `http://localhost:8000`; первоначальная настройка такая же, как на Windows.

Альтернативный запуск без моделей:

```bash
python3 run.py --without-ai
```

Остановка с сохранением данных:

```bash
python3 run.py --stop
```

## Место, память и ожидания

Первый запуск получает базовые образы, зависимости сборки и файлы моделей. Сборка worker может компилировать llama.cpp; этот этап дольше запуска готовых контейнеров. `--without-ai` пропускает worker и веса, но не Docker-сборку API.

В [config.yaml](../../config.yaml) закреплены размеры выбранных GGUF: около 3,14 ГБ для 4B, 6,58 ГБ для 9B и 13,51 ГБ для 27B; дополнительно нужны образы, модели поиска, кеш сборки и место для БД. Это размер файлов, а не требование к RAM. На ноутбуке с 24 ГБ начните с 9B; готовность 27B определяется реальным расходом памяти всего приложения. Docker Desktop также должен иметь достаточный доступ к памяти WSL 2.

`run.py --check` проверяет только инструменты хоста. Проверка не доказывает успешную сборку, вместимость модели, правильность бизнес-данных или доступность внешнего источника. После запуска проверьте `/ready`, `worker_ready` и известный вопрос к подключённой базе — [порядок проверки](README.md#как-понять-что-работает).

После перезагрузки запустите Docker Desktop на Windows; на Linux Engine запускается systemd, если включён указанной командой. Повторите обычную команду проекта. PostgreSQL приложения управляется Compose; отдельный нативный PostgreSQL ему не нужен.

## Подготовленная локальная копия Windows

В некоторых рабочих копиях существует `.tools/python/python.exe`. Это приватный локальный инструмент, исключённый из Git, а не часть установки из репозитория. Если файл уже есть, для launcher можно использовать его вместо установки ещё одного Python:

```powershell
.\.tools\python\python.exe run.py --check
.\.tools\python\python.exe run.py --model qwen3.5-9b
```

Docker по-прежнему обязателен. Embedded Python нельзя считать заменой обычного окружения разработки с `venv` и `pip`.

## Что проверено

В рабочем окружении Windows проверены Python-команды launcher, отказ при отсутствии Docker, компактные проверки запуска, оба demo через HTTP и подключение к нативному PostgreSQL. Docker Desktop на этой машине не установлен; его установку, сборку образов и полный запуск 9B здесь не выполняли. Linux-установка пакетов здесь также не выполнялась. Рецепты установки сверены с официальной документацией; CI отдельно проверяет Python на Windows/Linux и контейнерный запуск без AI. Успешный CI конкретной версии оценивайте по результатам workflow, а не по наличию этого описания.

Дальнейшая эксплуатация и подключение данных — [README](README.md), устройство контейнеров — [docker.md](docker.md), заполненная демонстрация — [demo](../demo/README.md).
