import hashlib
import json
from pathlib import Path


def ready(marker: Path, identity: dict, directory: Path) -> bool:
    try:
        saved = json.loads(marker.read_text(encoding="utf-8"))
        if (not isinstance(saved, dict) or saved.get("identity") != identity
                or not isinstance(saved.get("files"), dict) or not saved["files"]):
            return False
        if "files" in identity and saved["files"].keys() != identity["files"].keys():
            return False
        for name, expected in saved["files"].items():
            stat = (directory / name).stat()
            if [stat.st_size, stat.st_mtime_ns] != expected:
                return False
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def mark_ready(marker: Path, identity: dict, directory: Path, files: list[str]) -> None:
    manifest = {}
    for name in files:
        path = directory / name
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"Не завершена загрузка {path.name}.")
        stat = path.stat()
        manifest[name] = [stat.st_size, stat.st_mtime_ns]
    temporary = marker.with_suffix(".tmp")
    temporary.write_text(json.dumps({"identity": identity, "files": manifest}), encoding="utf-8")
    temporary.replace(marker)


def valid_gguf(path: Path, config: dict) -> bool:
    if not path.is_file() or path.stat().st_size != config["size_bytes"]:
        return False
    with path.open("rb") as handle:
        if handle.read(4) != b"GGUF":
            return False
        handle.seek(0)
        return hashlib.file_digest(handle, "sha256").hexdigest() == config["sha256"]


def ensure_llm(config: dict) -> None:
    path = Path(config["params"]["model_path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    marker = path.with_suffix(".ready.json")
    identity = {key: config[key] for key in ("repo_id", "revision", "sha256", "size_bytes")}
    if ready(marker, identity, path.parent):
        print(f"Готова: {path.name}", flush=True)
        return
    if not valid_gguf(path, config):
        from huggingface_hub import hf_hub_download

        print(f"Загрузка: {path.name}", flush=True)
        hf_hub_download(repo_id=config["repo_id"], filename=config["filename"],
                        revision=config["revision"], local_dir=path.parent,
                        force_download=path.exists())
        if not valid_gguf(path, config):
            raise ValueError(f"Размер или SHA-256 модели {path.name} не совпадает с каталогом.")
    mark_ready(marker, identity, path.parent, [path.name])


def valid_snapshot_file(path: Path, expected: str) -> bool:
    if not path.is_file():
        return False
    digest = hashlib.sha256() if len(expected) == 64 else hashlib.sha1()
    with path.open("rb") as handle:
        if len(expected) == 40:
            digest.update(f"blob {path.stat().st_size}\0".encode("ascii"))
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest() == expected


def ensure_snapshot(config: dict) -> None:
    directory = Path(config["cache_path"])
    directory.mkdir(parents=True, exist_ok=True)
    marker = directory / ".ready.json"
    identity = {key: config[key] for key in ("repo_id", "revision", "files")}
    if ready(marker, identity, directory):
        print(f"Готова: {config['repo_id']}", flush=True)
        return
    print(f"Подготовка: {config['repo_id']}", flush=True)
    for name, expected in config["files"].items():
        path = directory / name
        if valid_snapshot_file(path, expected):
            continue
        from huggingface_hub import hf_hub_download

        hf_hub_download(repo_id=config["repo_id"], filename=name,
                        revision=config["revision"], local_dir=directory,
                        force_download=path.exists())
        if not valid_snapshot_file(path, expected):
            raise ValueError(f"Контрольная сумма файла {name} не совпадает с каталогом.")
    mark_ready(marker, identity, directory, list(config["files"]))


def ensure_models(models: dict) -> None:
    ensure_llm(models["llm"])
    for name in ("embedder", "reranker"):
        ensure_snapshot(models[name])
