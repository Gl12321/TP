import math
import os
from pathlib import Path, PurePosixPath
import re

import yaml


BASE_DIR = Path(__file__).resolve().parents[2]


def load_config(path: Path | None = None) -> dict:
    source = path or Path(os.getenv("SQL_AGENT_CONFIG", BASE_DIR / "config.yaml"))
    try:
        with source.open(encoding="utf-8") as handle:
            config = yaml.safe_load(handle)
    except yaml.YAMLError as error:
        raise ValueError(f"Не удалось прочитать YAML в {source.name}: {error}") from error
    if not isinstance(config, dict):
        raise ValueError("config.yaml должен содержать настройки приложения.")
    required = {"model", "settings", "generation", "models", "retrieval_models"}
    if set(config) != required:
        raise ValueError("Разделы config.yaml: " + ", ".join(sorted(required)))
    for name in required - {"model"}:
        if not isinstance(config[name], dict):
            raise ValueError(f"Раздел {name} должен быть словарём.")
    if not isinstance(config["model"], str) or config["model"] not in config["models"]:
        raise ValueError("Модель по умолчанию отсутствует в каталоге models.")
    for name, model in config["models"].items():
        if (not isinstance(model, dict) or not isinstance(model.get("size_bytes"), int)
                or model["size_bytes"] <= 0
                or not re.fullmatch(r"[a-f0-9]{64}", str(model.get("sha256", "")))
                or not re.fullmatch(r"[a-f0-9]{40}", str(model.get("revision", "")))):
            raise ValueError(f"У модели {name} нужны размер, SHA-256 и точная версия репозитория.")
        filename = model.get("filename")
        if (not isinstance(filename, str) or not filename.endswith(".gguf")
                or Path(filename).name != filename or any(char in filename for char in "\\/:")):
            raise ValueError(f"filename модели {name} должен быть именем одного GGUF-файла.")
    if set(config["retrieval_models"]) != {"embedder", "reranker"}:
        raise ValueError("retrieval_models должен содержать embedder и reranker.")
    for name, model in config["retrieval_models"].items():
        if (not isinstance(model, dict)
                or not re.fullmatch(r"[a-f0-9]{40}", str(model.get("revision", "")))
                or not isinstance(model.get("files"), dict) or not model["files"]
                or type(model.get("max_length")) is not int or model["max_length"] <= 0):
            raise ValueError(f"Для {name} нужны точная версия, файлы с контрольными суммами и max_length.")
        for filename, digest in model["files"].items():
            if (not isinstance(filename, str) or not filename or "\\" in filename or ":" in filename
                    or PurePosixPath(filename).is_absolute()
                    or any(part in {"", ".", ".."} for part in filename.split("/"))
                    or not re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", str(digest))):
                raise ValueError(f"У {name} неверный путь или контрольная сумма файла {filename!r}.")
    for name, model in {**config["models"], **config["retrieval_models"]}.items():
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", str(model.get("repo_id", ""))):
            raise ValueError(f"repo_id модели {name} должен иметь вид владелец/репозиторий.")
    return config


def resolve_models(config: dict, preset: str, root: Path = BASE_DIR) -> dict:
    if preset not in config["models"]:
        raise ValueError(f"Неизвестная модель {preset!r}. Доступны: {', '.join(config['models'])}")
    llm = dict(config["models"][preset])
    filename = llm["filename"]
    if not isinstance(filename, str) or Path(filename).name != filename or "\\" in filename:
        raise ValueError("filename модели должен быть именем одного GGUF-файла.")
    directory = root / "models"
    params = {**config["generation"], **llm.get("params", {})}
    threads = max(1, min(8, (os.cpu_count() or 2) - 2))
    for name in ("n_threads", "n_threads_batch"):
        if params.get(name) is None:
            params[name] = threads
    for name in ("max_tokens", "n_ctx", "n_batch", "n_ubatch", "n_threads", "n_threads_batch"):
        value = params.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"generation.{name} должен быть положительным целым числом.")
    if params["max_tokens"] + 32 >= params["n_ctx"]:
        raise ValueError("n_ctx должен вмещать max_tokens и промпт с резервом 32 токена.")
    if params["n_batch"] > params["n_ctx"] or params["n_ubatch"] > params["n_batch"]:
        raise ValueError("Нужны n_ubatch <= n_batch <= n_ctx.")
    temperature = params.get("temperature")
    if (isinstance(temperature, bool) or not isinstance(temperature, (int, float))
            or not math.isfinite(temperature) or temperature < 0):
        raise ValueError("temperature должна быть конечным неотрицательным числом.")
    gpu_layers = params.get("n_gpu_layers")
    if isinstance(gpu_layers, bool) or not isinstance(gpu_layers, int) or gpu_layers < -1:
        raise ValueError("n_gpu_layers должен быть целым числом не меньше -1.")
    params["model_path"] = str(directory / filename)
    llm["params"] = params
    result = {"llm": llm}
    for name in ("embedder", "reranker"):
        result[name] = {**config["retrieval_models"][name], "cache_path": str(directory / name)}
    return result
