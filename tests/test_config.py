from copy import deepcopy

import pytest
import yaml

from runtime.config import load_config, resolve_models


def test_model_profiles_resolve_inside_selected_project(tmp_path):
    config = load_config()
    assert set(config["models"]) == {"qwen3.5-4b", "qwen3.5-9b", "qwen3.5-27b"}
    for preset in config["models"]:
        model = resolve_models(config, preset, tmp_path)["llm"]
        assert model["params"]["model_path"] == str(tmp_path / "models" / model["filename"])
    with pytest.raises(ValueError):
        resolve_models(config, "missing")


@pytest.mark.parametrize(
    "section,values",
    [
        ("generation", {"n_ctx": -1}),
        ("generation", {"max_tokens": 100000}),
        ("settings", {"MIN_FREE_MEMORY_MB": -1}),
        ("settings", {"RERANKER_BATCH_SIZE": True}),
        ("retrieval_models", {"embedder": {"files": {"../secret": "a" * 40}}}),
    ],
)
def test_invalid_configuration_is_rejected_before_runtime(tmp_path, section, values):
    config = deepcopy(load_config())
    if section == "retrieval_models":
        config[section]["embedder"].update(values["embedder"])
    else:
        config[section].update(values)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(ValueError):
        resolve_models(load_config(path), config["model"])
