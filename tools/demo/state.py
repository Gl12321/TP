import json
from dataclasses import dataclass
import os
from pathlib import Path
import re
import secrets
import socket
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[2]
ROLES = {
    "director": "Анна · директор сети",
    "regional_manager": "Михаил · руководитель региона",
    "franchise_owner": "Ирина · владелец франшизы",
    "store_manager": "Сергей · управляющий точкой",
    "analyst": "Елена · аналитик",
    "admin": "Денис · администратор",
}


class DemoError(RuntimeError):
    pass


@dataclass(frozen=True)
class DemoFiles:
    directory: Path

    @classmethod
    def for_scenario(cls, scenario):
        return cls(ROOT / ".runtime" / ("demo" if scenario == "network" else "demo-onboarding"))

    def path(self, name):
        return self.directory / (name + ".json")


def save(path, value):
    temporary = path.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def load(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise DemoError(f"Не удалось прочитать {path}. Существующие данные не изменены.") from error


def validate(state):
    try:
        suffix = state["id"]
        parsed = urlsplit(state["database_url"])
        valid = (
            state["version"] == 1
            and state.get("scenario", "network") in {"network", "onboarding"}
            and re.fullmatch(r"[0-9a-f]{12}", suffix)
            and parsed.scheme == "postgresql+asyncpg"
            and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            and parsed.path == "/razbor_demo_" + suffix
            and parsed.username == "razbor_demo_app_" + suffix
            and bool(parsed.password)
            and 1 <= state["port"] <= 65535
            and len(state["secret_key"]) >= 32
            and len(state["bootstrap_token"]) >= 32
        )
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise DemoError("Некорректный state.json demo; запуск остановлен без изменений.")


def new_state(admin_url, port, scenario="network"):
    parsed = urlsplit(admin_url)
    suffix = secrets.token_hex(6)
    role = "razbor_demo_app_" + suffix
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    return {
        "version": 1,
        "scenario": scenario,
        "id": suffix,
        "port": port,
        "ready": False,
        "database_url": f"postgresql+asyncpg://{role}:{secrets.token_urlsafe(32)}@{host}:{parsed.port or 5432}/razbor_demo_{suffix}",
        "secret_key": secrets.token_urlsafe(32),
        "bootstrap_token": secrets.token_urlsafe(32),
    }


def new_accounts(port, scenario="network"):
    accounts = [
        {
            "role": role,
            "name": name,
            "email": role.replace("_", "-") + "@demo.example.test",
            "password": secrets.token_urlsafe(24),
        }
        for role, name in ROLES.items()
        if scenario == "network" or role == "director"
    ]
    return {
        "url": f"http://127.0.0.1:{port}",
        "email": accounts[0]["email"],
        "password": accounts[0]["password"],
        "accounts": accounts,
    }


def environment(state, files):
    return {
        "APP_DATABASE_URL": state["database_url"],
        "APP_SECRET_KEY": state["secret_key"],
        "APP_BOOTSTRAP_TOKEN": state["bootstrap_token"],
        "APP_SECURE_COOKIES": "false",
        "APP_ALLOWED_ORIGINS": f"http://127.0.0.1:{state['port']}",
        "APP_SOURCE_HOSTS": urlsplit(state["database_url"]).hostname,
        "APP_CONFIG_PATH": str(ROOT / "config.yaml"),
        "APP_FRONTEND_DIST": str(ROOT / "frontend" / "dist"),
        "APP_INDEX_DIR": str(files.directory / "indexes"),
    }


def preflight(port):
    if not (ROOT / "frontend" / "dist" / "index.html").is_file():
        raise DemoError("Сначала соберите frontend: npm --prefix frontend run build.")
    try:
        with socket.socket() as connection:
            if os.name == "nt":
                connection.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            connection.bind(("127.0.0.1", port))
    except OSError as error:
        raise DemoError(
            f"Порт {port} занят или недоступен; существующий сервер не остановлен."
        ) from error


def check_files(state, files):
    if not state.get("ready"):
        raise DemoError(
            f"Подготовка demo не завершена. Проверьте {files.directory}; автоматический сброс запрещён."
        )
    accounts = load(files.path("accounts"))
    network = state.get("scenario", "network") == "network"
    source = load(files.path("source")) if network else None
    try:
        roles = {item["role"] for item in accounts["accounts"]}
        valid = (
            roles == (set(ROLES) if network else {"director"})
            and accounts["url"] == f"http://127.0.0.1:{state['port']}"
            and accounts["workspace_id"] == state["workspace_id"]
            and (not network or source["source"]["database"] == state["source_database"])
            and all(len(item["password"]) >= 12 for item in accounts["accounts"])
        )
    except (KeyError, TypeError):
        valid = False
    if not valid:
        raise DemoError(
            "Файлы состояния demo не согласованы. Автоматическое пересоздание отключено."
        )
    return accounts, source
