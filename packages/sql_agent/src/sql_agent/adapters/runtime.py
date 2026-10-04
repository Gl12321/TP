from pathlib import Path

from sql_agent.adapters.concurrency import finish_in_thread as finish_in_thread


def model_source(config: dict) -> tuple[str, str | None]:

    for key in ("local_path", "local_dir", "cache_path"):
        value = config.get(key)
        if value and (Path(value) / "config.json").is_file():
            return str(Path(value)), None
    if not config.get("repo_id"):
        raise ValueError("Model config requires a local model directory or repo_id.")
    return config["repo_id"], config.get("cache_path")
