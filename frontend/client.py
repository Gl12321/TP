from collections import deque
import json
from threading import Event, Lock, Thread
import time
from urllib.parse import urlsplit
from uuid import uuid4

import requests


class ApiError(Exception):
    def __init__(self, message: str, *, loaded_schemas: list[str] | None = None):
        super().__init__(message)
        self.loaded_schemas = loaded_schemas or []


def _read_json(response, limit: int = 1024 * 1024):
    body = bytearray()
    for chunk in response.iter_content(chunk_size=8192):
        body.extend(chunk)
        if len(body) > limit:
            raise ApiError("Ответ API превышает допустимый размер.")
    try:
        return json.loads(body)
    except (ValueError, UnicodeError) as error:
        raise ApiError("API вернул ответ неизвестного формата.") from error


def _check_response(response):
    if 200 <= response.status_code < 300:
        return
    try:
        body = _read_json(response)
    except ApiError:
        body = {}
    detail = body.get("detail", body) if isinstance(body, dict) else {}
    loaded = detail.get("loaded_schemas", []) if isinstance(detail, dict) else []
    if response.status_code in {401, 403}:
        message = "API отклонил токен доступа. Проверьте API_TOKEN."
    elif isinstance(detail, dict):
        message = str(detail.get("message") or f"Ошибка API: HTTP {response.status_code}.")
    elif isinstance(detail, str):
        message = detail
    elif isinstance(detail, list):
        message = "API отклонил параметры запроса. Проверьте вопрос, схемы и файлы."
    else:
        message = f"Ошибка API: HTTP {response.status_code}."
    raise ApiError(message[:2000], loaded_schemas=loaded)


class _MultipartFiles:


    def __init__(self, files, max_file_bytes: int):
        self.boundary = uuid4().hex
        self.parts, self.length, total_bytes = [], 0, 0
        for file in files:
            name = file.name
            if not name or any(char in name for char in ('\r', '\n', '\x00')):
                raise ApiError("Недопустимое имя файла.")
            file.seek(0, 2)
            size = file.tell()
            file.seek(0)
            if size <= 0 or size > max_file_bytes:
                raise ApiError(f"Файл {name!r} пуст или превышает лимит загрузки.")
            total_bytes += size
            if total_bytes > max_file_bytes:
                raise ApiError("Общий размер файлов превышает лимит одной загрузки.")
            quoted = name.replace("\\", "\\\\").replace('"', '\\"')
            header = (f"--{self.boundary}\r\n"
                      f'Content-Disposition: form-data; name="files"; filename="{quoted}"\r\n'
                      "Content-Type: application/octet-stream\r\n\r\n").encode("utf-8")
            self.parts.append((header, file, size))
            self.length += len(header) + size + 2
        if not 1 <= len(self.parts) <= 8:
            raise ApiError("Выберите от 1 до 8 SQLite-файлов.")
        self.ending = f"--{self.boundary}--\r\n".encode("ascii")
        self.length += len(self.ending)

    def __len__(self):
        return self.length

    def __iter__(self):
        for header, file, size in self.parts:
            yield header
            file.seek(0)
            remaining = size
            while remaining:
                chunk = file.read(min(64 * 1024, remaining))
                if not chunk:
                    raise ApiError("Размер файла изменился во время загрузки.")
                remaining -= len(chunk)
                yield chunk
            yield b"\r\n"
        yield self.ending


class ApiClient:
    def __init__(self, url: str, token: str):
        self.url = url.strip().rstrip("/")
        try:
            parsed = urlsplit(self.url)
            parsed.port
        except ValueError as error:
            raise ApiError("Некорректный адрес API.") from error
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
            raise ApiError("Укажите HTTP(S)-адрес API без параметров и фрагмента.")
        if parsed.username or parsed.password:
            raise ApiError("Токен задаётся отдельно от адреса API.")
        if not token.strip():
            raise ApiError("Укажите токен доступа к API.")
        if not token.strip().isascii() or not token.strip().isprintable():
            raise ApiError("Токен доступа должен содержать печатные ASCII-символы.")
        self.headers = {"Authorization": f"Bearer {token.strip()}"}

    def request(self, method: str, path: str, *, timeout: float = 20, **kwargs):
        headers = {**self.headers, **kwargs.pop("headers", {})}
        try:
            with requests.request(method, self.url + path, headers=headers, timeout=(5, timeout),
                                  stream=True, allow_redirects=False, **kwargs) as response:
                _check_response(response)
                return _read_json(response)
        except requests.Timeout as error:
            raise ApiError("API не ответил вовремя. Операция на сервере могла продолжиться; обновите список схем.") from error
        except requests.RequestException as error:
            raise ApiError("Не удалось связаться с API. Проверьте адрес и состояние сервера.") from error

    def schemas(self):
        value = self.request("GET", "/schema_show")
        if (not isinstance(value, dict) or not isinstance(value.get("schemas"), list)
                or any(not isinstance(name, str) for name in value["schemas"])):
            raise ApiError("API вернул некорректный список схем.")
        dirty = value.get("unindexed_schemas", [])
        if not isinstance(dirty, list) or any(not isinstance(name, str) for name in dirty):
            raise ApiError("API вернул некорректный список устаревших схем.")
        return value

    def upload(self, files, max_file_bytes: int):
        body = _MultipartFiles(files, max_file_bytes)
        return self.request("POST", "/load_schema", timeout=600, data=body, headers={
            "Content-Type": f"multipart/form-data; boundary={body.boundary}",
            "Content-Length": str(len(body)),
        })

    def cancel(self, request_id: str) -> bool:
        try:
            with requests.post(f"{self.url}/queries/{request_id}/cancel", headers=self.headers,
                               timeout=(3, 5), stream=True, allow_redirects=False) as response:
                if response.status_code == 404:
                    return False
                _check_response(response)
                return True
        except requests.RequestException as error:
            raise ApiError("Сервер не подтвердил отмену; поток будет закрыт.") from error


def _events(response, max_event_bytes: int, stop: Event | None = None):
    pending = bytearray()
    for chunk in response.iter_content(chunk_size=8192):
        if stop is not None and stop.is_set():
            return
        pending.extend(chunk)
        while True:
            end = pending.find(b"\n")
            if end < 0:
                break
            if end > max_event_bytes:
                raise ApiError("Событие API превышает лимит размера.")
            line = bytes(pending[:end])
            del pending[:end + 1]
            if line.strip():
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeError) as error:
                    raise ApiError("Повреждено событие потока API.") from error
                if not isinstance(event, dict) or not isinstance(event.get("content"), dict):
                    raise ApiError("Неизвестная структура события API.")
                yield event
        if len(pending) > max_event_bytes:
            raise ApiError("Событие API превышает лимит размера.")
    if pending.strip():
        raise ApiError("Поток API завершился посреди события.")


class QueryJob:


    def __init__(self, client: ApiClient, question: str, schemas: list[str], *,
                 progress_limit: int = 60, lease_seconds: float = 45,
                 max_event_bytes: int = 16 * 1024 * 1024):
        self.client, self.question, self.schemas = client, question, tuple(schemas)
        self.local_id = uuid4().hex
        self.done, self.cancel_requested = Event(), Event()
        self._close_stream = Event()
        self._lock = Lock()
        self._progress = deque(maxlen=progress_limit)
        self._terminal = self._request_id = self._cancel_note = None
        self._last_seen = self._started = time.monotonic()
        self._lease_seconds, self._max_event_bytes = lease_seconds, max_event_bytes
        Thread(target=self._run, name=f"sql-stream-{self.local_id[:8]}", daemon=True).start()
        Thread(target=self._watch, name=f"sql-cancel-{self.local_id[:8]}", daemon=True).start()

    def touch(self):
        with self._lock:
            self._last_seen = time.monotonic()

    def snapshot(self):
        with self._lock:
            return {"progress": list(self._progress), "terminal": self._terminal,
                    "request_id": self._request_id, "cancel_note": self._cancel_note,
                    "elapsed": time.monotonic() - self._started}

    def cancel(self):
        self.cancel_requested.set()

    def _finish(self, event):
        with self._lock:
            if self._terminal is None:
                self._terminal = event

    def _run(self):
        try:
            with requests.post(self.client.url + "/question/stream", headers=self.client.headers,
                               json={"question": self.question, "schemas_for_search": list(self.schemas)},
                               stream=True, timeout=(5, 15), allow_redirects=False) as response:
                _check_response(response)
                for event in _events(response, self._max_event_bytes, self._close_stream):
                    kind, request_id = event.get("event"), event.get("request_id")
                    if (not isinstance(request_id, str) or not request_id or len(request_id) > 128
                            or not all(char.isascii() and (char.isalnum() or char in "_-") for char in request_id)):
                        raise ApiError("API не передал корректный идентификатор запроса.")
                    with self._lock:
                        if self._request_id is not None and self._request_id != request_id:
                            raise ApiError("Получено событие другого запроса.")
                        self._request_id = request_id
                    if kind in {"result", "error"}:
                        self._finish(event)
                        break
                    if kind == "stage":
                        content = event["content"]
                        with self._lock:
                            self._progress.append({"stage": str(content.get("stage", ""))[:80],
                                                   "message": str(content.get("message", ""))[:1000]})
                    elif kind not in {"accepted", "heartbeat"}:
                        raise ApiError("API прислал неизвестный тип события.")
                else:
                    if not self._close_stream.is_set():
                        raise ApiError("Поток API закончился без результата.")
        except (ApiError, requests.RequestException, OSError, ValueError) as error:
            if not self._close_stream.is_set():
                message = str(error) if isinstance(error, ApiError) else "Соединение с API прервано."
                self._finish({"event": "error", "content": {"code": "connection_error", "message": message}})
        finally:
            cancelled = self._close_stream.is_set()
            self._finish({"event": "error", "content": {
                "code": "cancelled" if cancelled else "connection_error",
                "message": "Отмена запрошена. Поток закрыт; сервер освобождает ресурсы."
                           if cancelled else "Не удалось получить результат API.",
            }})
            self.done.set()

    def _watch(self):
        while not self.done.wait(0.25):
            with self._lock:
                stale = time.monotonic() - self._last_seen > self._lease_seconds
                expired = time.monotonic() - self._started > 1800
                request_id = self._request_id
            if stale or expired:
                self.cancel_requested.set()
            if self.cancel_requested.is_set() and request_id is not None:
                close_stream = True
                try:
                    close_stream = self.client.cancel(request_id)
                except ApiError as error:
                    with self._lock:
                        self._cancel_note = str(error)
                finally:
                    if close_stream:
                        self._close_stream.set()
                return
