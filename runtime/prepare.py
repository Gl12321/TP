import argparse
import os
import sys

from runtime.config import load_config, resolve_models
from runtime.models import ensure_models


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Подготовка локальных моделей Разбора.")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--list-models", action="store_true")
    action.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        config = load_config()
        selected = os.environ.get("MODEL_PRESET") or config["model"]
        models = resolve_models(config, selected)
        if args.list_models:
            for name, model in config["models"].items():
                default = " (по умолчанию)" if name == config["model"] else ""
                print(
                    f"{name:18} {model['size_bytes'] / 1024**3:5.2f} ГиБ GGUF  {model['label']}{default}"
                )
            return 0
        print(f"Выбрана модель: {selected}", flush=True)
        if not args.check:
            ensure_models(models)
        return 0
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        print(f"Подготовка не завершена: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
