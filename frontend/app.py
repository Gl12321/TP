import hashlib
import json
import os
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

from client import ApiClient, ApiError, QueryJob


st.set_page_config(page_title="Text to SQL", page_icon="📊", layout="wide")
if not hasattr(st, "fragment"):
    st.error("Для интерфейса нужен Streamlit 1.37 или новее: отсутствует st.fragment.")
    st.stop()
try:
    config_path = Path(os.getenv("SQL_AGENT_CONFIG", Path(__file__).resolve().parents[1] / "config.yaml"))
    with config_path.open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    max_upload_bytes = int(os.getenv("MAX_UPLOAD_BYTES", config["settings"]["MAX_UPLOAD_BYTES"]))
    if max_upload_bytes <= 0:
        raise ValueError
except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError):
    st.error("Проверьте config.yaml: MAX_UPLOAD_BYTES должен быть положительным целым числом.")
    st.stop()


def notice(kind: str, message: str):
    st.session_state["notice"] = (kind, message)


def refresh_schemas(client: ApiClient) -> bool:
    try:
        value = client.schemas()
    except ApiError as error:
        st.session_state["schemas"] = []
        st.session_state["dirty_schemas"] = []
        message = "Не удалось обновить список схем. Повторите обновление перед запросом. " + str(error)
        previous = st.session_state.get("notice")
        if previous is not None:
            message = previous[1] + "\n\n" + message
        notice("error", message)
        return False
    st.session_state["schemas"] = sorted(value["schemas"])
    st.session_state["dirty_schemas"] = value.get("unindexed_schemas", [])
    return True


def report_error(error: ApiError):
    message = str(error)
    if error.loaded_schemas:
        message += " Уже загружены: " + ", ".join(error.loaded_schemas) + "."
    notice("error", message)


def render_result(event):
    content = event["content"]
    if event["event"] == "error":
        message = content.get("message", "Не удалось выполнить запрос.")
        (st.info if content.get("code") == "cancelled" else st.error)(message)
        return
    render_table(content)
    if content.get("sql"):
        completed = content.get("status") in {"success", "empty"}
        with st.expander("SQL этого результата" if completed else "Последний сформированный SQL"):
            st.caption("Этот запрос выполнен для получения результата." if completed
                       else "Обработка завершилась без результата. Этот запрос не подтверждён как успешно выполненный.")
            st.code(content["sql"], language="sql")


def render_table(content):
    status = content.get("status")
    if status not in {"success", "empty"}:
        message = content.get("error") or f"Запрос завершён со статусом {status}."
        (st.info if status in {"cancelled", "not_found"} else st.error)(message)
        return
    columns, rows = content.get("columns", []), content.get("rows", [])
    if (not isinstance(columns, list) or not all(isinstance(name, str) for name in columns)
            or not isinstance(rows, list) or len(rows) > 10_000
            or any(not isinstance(row, list) or len(row) != len(columns) for row in rows)):
        st.error("API вернул некорректную таблицу или превысил предел интерфейса в 10 000 строк.")
        return
    if content.get("truncated"):
        st.warning("Показана часть результата: достигнут лимит строк или размера. Уточните фильтры в вопросе.")
    if not rows:
        st.info("Запрос выполнен. Подходящих строк нет.")
        return
    labels, used = [], set()
    for name in columns:
        label, suffix = name, 2
        while label in used:
            label = f"{name} [{suffix}]"
            suffix += 1
        labels.append(label)
        used.add(label)
    values = [[json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
               for value in row] for row in rows]
    st.subheader("Результат")
    st.caption(f"Строк: {len(rows)} · Попыток генерации: {content.get('attempts', 1)}")
    if labels != columns:
        st.caption("Повторяющиеся названия колонок дополнены номером только для отображения.")
    st.dataframe(pd.DataFrame(values, columns=labels, dtype=object), width="stretch", hide_index=True)


for key, value in {"schemas": [], "dirty_schemas": [], "upload_generation": 0}.items():
    if key not in st.session_state:
        st.session_state[key] = value
if st.session_state.pop("reset_delete_confirmation", False):
    st.session_state["confirm_delete"] = False

job = st.session_state.get("job")
running = job is not None and not job.done.is_set()
if running:
    job.touch()

with st.sidebar:
    st.header("Подключение")
    api_url = st.text_input("Адрес API", value=os.getenv("API_URL", "http://localhost:8000"), disabled=running)
    entered_token = st.text_input("Токен доступа", type="password", disabled=running,
                                 help="Если поле пустое, используется API_TOKEN из окружения.")
    token = entered_token.strip() or os.getenv("API_TOKEN", "")
    client = None
    try:
        client = ApiClient(api_url, token)
    except ApiError as error:
        st.caption(str(error))

    fingerprint = (api_url, hashlib.sha256(token.encode("utf-8")).hexdigest())
    if st.session_state.get("connection") != fingerprint and not running:
        st.session_state["connection"] = fingerprint
        st.session_state["schemas"] = []
        st.session_state["dirty_schemas"] = []
        st.session_state.pop("chosen_schemas", None)
        st.session_state["confirm_delete"] = False
        st.session_state.pop("job", None)
        if client is not None:
            refresh_schemas(client)

    st.header("Схемы данных")
    if st.button("Обновить список", disabled=running or client is None):
        if refresh_schemas(client):
            notice("success", "Список схем обновлён.")
        st.rerun()

    dirty = set(st.session_state["dirty_schemas"])
    usable = [name for name in st.session_state["schemas"] if name not in dirty]
    if "chosen_schemas" not in st.session_state:
        st.session_state["chosen_schemas"] = usable[:100]
    else:
        st.session_state["chosen_schemas"] = [name for name in st.session_state["chosen_schemas"] if name in usable]
    selected = st.multiselect("Искать в схемах", usable, key="chosen_schemas", disabled=running)
    if dirty:
        st.warning("Требуют переиндексации: " + ", ".join(sorted(dirty)))
    if not st.session_state["schemas"]:
        st.caption("Загрузите SQLite или обновите список после подключения.")

    with st.expander("Загрузить SQLite"):
        uploads = st.file_uploader("Файлы .db, .sqlite, .sqlite3", type=["db", "sqlite", "sqlite3"],
                                   max_upload_size=(max_upload_bytes + 1024 ** 2 - 1) // 1024 ** 2,
                                   accept_multiple_files=True, disabled=running,
                                   key=f"uploads_{st.session_state['upload_generation']}")
        st.caption("Имя файла становится именем схемы. Повторная загрузка заменяет её данные.")
        st.caption(f"До 8 файлов, суммарно до {max_upload_bytes / (1024 * 1024):g} МиБ. "
                   "Выбранные файлы занимают оперативную память ещё до импорта.")
        if st.button("Загрузить и проиндексировать", disabled=running or client is None or not uploads):
            st.session_state["reset_delete_confirmation"] = True
            try:
                with st.spinner("Импорт и индексация…"):
                    result = client.upload(uploads, max_upload_bytes)
                notice("success", "Загружены схемы: " + ", ".join(result.get("loaded_schemas", [])))
                st.session_state["upload_generation"] += 1
            except ApiError as error:
                report_error(error)
            refresh_schemas(client)
            st.rerun()

    with st.expander("Обслуживание"):
        if st.button("Перестроить индекс", disabled=running or client is None):
            st.session_state["reset_delete_confirmation"] = True
            try:
                with st.spinner("Переиндексация…"):
                    client.request("POST", "/schemas/reindex", timeout=600)
                notice("success", "Индекс перестроен.")
            except ApiError as error:
                report_error(error)
            refresh_schemas(client)
            st.rerun()
        confirm = st.checkbox("Подтверждаю удаление всех пользовательских схем и их данных",
                              disabled=running, key="confirm_delete")
        st.caption("Будут удалены также схемы, созданные вне приложения. Системные схемы и public сохраняются.")
        if st.button("Удалить все схемы", disabled=running or client is None or not confirm):
            st.session_state["reset_delete_confirmation"] = True
            try:
                client.request("DELETE", "/drop_all_schemas", timeout=120)
                notice("success", "Пользовательские схемы удалены.")
            except ApiError as error:
                report_error(error)
            refresh_schemas(client)
            st.rerun()

st.title("Вопросы к данным")
st.caption("Выберите схемы, задайте вопрос и проверьте сформированный SQL.")
if "notice" in st.session_state:
    kind, message = st.session_state.pop("notice")
    getattr(st, kind)(message)

with st.form("question_form", clear_on_submit=False):
    question = st.text_area("Вопрос", max_chars=4000, disabled=running,
                            placeholder="Например: покажи выручку по месяцам за последний год")
    submitted = st.form_submit_button("Выполнить", disabled=running or client is None or not selected)
if submitted:
    if not question.strip():
        st.warning("Введите вопрос.")
    elif len(selected) > 100:
        st.warning("Выберите не более 100 схем.")
    else:
        st.session_state["job"] = QueryJob(client, question.strip(), selected)
        st.session_state["completed_job"] = None
        st.session_state["upload_generation"] += 1
        st.rerun()


@st.fragment(run_every=0.5 if running else None)
def monitor():
    active = st.session_state.get("job")
    if active is None:
        return
    active.touch()
    snapshot, finished = active.snapshot(), active.done.is_set()
    if finished and st.session_state.get("completed_job") != active.local_id:
        st.session_state["completed_job"] = active.local_id
        st.rerun()
    st.subheader("Последний запрос")
    st.write(active.question)
    st.caption("Схемы: " + ", ".join(active.schemas))
    if not finished:
        if st.button("Отменить запрос", disabled=active.cancel_requested.is_set(), key="cancel_query"):
            active.cancel()
        if active.cancel_requested.is_set():
            st.info("Отмена запрошена. Ожидаем освобождения ресурсов сервера.")
        else:
            st.caption(f"Выполняется · {int(snapshot['elapsed'])} с")
    if snapshot["request_id"]:
        st.caption("ID запроса: " + snapshot["request_id"])
    with st.expander("Ход обработки", expanded=not finished):
        if snapshot["progress"]:
            st.code("\n".join(item["message"] for item in snapshot["progress"]), language="text")
        else:
            st.caption("Ожидаем первый этап обработки.")
    if snapshot["cancel_note"]:
        st.warning(snapshot["cancel_note"])
    if finished and snapshot["terminal"] is not None:
        render_result(snapshot["terminal"])
    if finished and st.button("Очистить результат", key="clear_result"):
        st.session_state.pop("job", None)
        st.rerun()


monitor()
