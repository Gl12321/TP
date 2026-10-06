import asyncio
from collections.abc import Mapping
import math
from pathlib import Path
from threading import Lock
import time
from typing import Any

from sql_agent.adapters.concurrency import finish_in_thread
from sql_agent.adapters.memory import available_memory
from sql_agent.query import QueryContext, QueryError
from sql_agent.generation.template import ChatTemplate


class LLMClient:
    def __init__(
        self,
        model_config: dict[str, Any],
        *,
        timeout_seconds: float = 180,
        min_free_memory_mb: int = 2048,
        runtime_memory_mb: int = 1024,
    ):
        if not isinstance(model_config, Mapping) or not isinstance(
            model_config.get("params"), Mapping
        ):
            raise QueryError("model_config", "Настройки модели должны содержать словарь params.")
        params = dict(model_config["params"])
        try:
            model_path = Path(params["model_path"])
        except (KeyError, TypeError, ValueError) as exc:
            raise QueryError("model_config", "В params нужен локальный model_path.") from exc
        if not model_path.is_file():
            raise QueryError(
                "model_missing",
                f"Файл модели не найден: {model_path.name}. Загрузите его отдельно.",
            )
        self.max_tokens = self._positive_int(params.pop("max_tokens", 512), "max_tokens")
        self.context_size = self._positive_int(params.setdefault("n_ctx", 4096), "n_ctx")
        self.min_free_memory_mb = self._positive_int(min_free_memory_mb, "min_free_memory_mb")
        self.runtime_memory_mb = self._positive_int(runtime_memory_mb, "runtime_memory_mb")
        self.temperature = self._finite_number(
            params.pop("temperature", 0.0), "temperature", minimum=0
        )
        self.timeout_seconds = self._finite_number(
            timeout_seconds, "timeout_seconds", minimum=0, strict=True
        )
        params.setdefault("n_batch", min(256, self.context_size))
        self._positive_int(params["n_batch"], "n_batch")
        for name in ("n_ubatch", "n_threads", "n_threads_batch"):
            if name in params and params[name] is not None:
                self._positive_int(params[name], name)
        if params.get("n_ubatch", params["n_batch"]) > params["n_batch"]:
            raise QueryError("model_config", "n_ubatch не должен превышать n_batch.")
        gpu_layers = params.get("n_gpu_layers", 0)
        if isinstance(gpu_layers, bool) or not isinstance(gpu_layers, int) or gpu_layers < -1:
            raise QueryError("model_config", "n_gpu_layers должен быть целым числом не меньше -1.")
        if params["n_batch"] > self.context_size:
            raise QueryError("model_config", "n_batch не должен превышать n_ctx.")
        if self.max_tokens + 32 >= self.context_size:
            raise QueryError(
                "model_config", "n_ctx должен вмещать промпт и max_tokens с резервом 32 токена."
            )
        params["model_path"] = str(model_path)
        self._gate = asyncio.Lock()
        self._tokenizer_lock = Lock()
        self._closed = False
        self._check_memory(
            reserve_bytes=model_path.stat().st_size + self.runtime_memory_mb * 1024 * 1024
        )
        from llama_cpp import Llama

        self.model = Llama(**params)
        try:
            template = self.model.metadata.get("tokenizer.chat_template")
            self._eos_id, self._bos_id = self.model.token_eos(), self.model.token_bos()
            self._eos = self._decode_special(self._eos_id)
            if self._eos_id < 0 or not self._eos:
                raise QueryError("model_config", "Модель не предоставляет корректный EOS-токен.")
            self.template = ChatTemplate(
                template,
                eos_token=self._eos,
                bos_token=self._decode_special(self._bos_id),
            )
            bos_setting = self.model.metadata.get("tokenizer.ggml.add_bos_token")
            if isinstance(bos_setting, bool):
                self._add_bos = bos_setting
            elif isinstance(bos_setting, str) and bos_setting.lower() in {"true", "false"}:
                self._add_bos = bos_setting.lower() == "true"
            else:
                defaults = self.model.tokenize(b"", add_bos=True, special=True)
                self._add_bos = self._bos_id >= 0 and self._bos_id in defaults[:1]
            if self._add_bos and self._bos_id < 0:
                raise QueryError(
                    "model_config",
                    "Токенизатор требует BOS, но модель не предоставляет этот токен.",
                )
            self.context_size = self.model.n_ctx()
            if self.max_tokens + 32 >= self.context_size:
                raise QueryError(
                    "model_config", "Фактический контекст модели слишком мал для max_tokens."
                )
            self._check_memory()
        except BaseException:
            self.model.close()
            self._closed = True
            raise

    @staticmethod
    def _positive_int(value: Any, name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise QueryError("model_config", f"{name} должен быть положительным целым числом.")
        return value

    @staticmethod
    def _finite_number(value: Any, name: str, *, minimum: float, strict: bool = False) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise QueryError("model_config", f"{name} должен быть конечным числом.")
        try:
            finite = math.isfinite(value)
        except OverflowError:
            finite = False
        if not finite:
            raise QueryError("model_config", f"{name} должен быть конечным числом.")
        if value < minimum or (strict and value == minimum):
            raise QueryError("model_config", f"Недопустимое значение {name}.")
        return float(value)

    def _decode_special(self, token: int) -> str:
        return self.model.detokenize([token], special=True).decode("utf-8") if token >= 0 else ""

    def _check_memory(self, *, reserve_bytes: int = 0) -> None:
        required = reserve_bytes + self.min_free_memory_mb * 1024 * 1024
        if available_memory() < required:
            raise QueryError(
                "memory_limit", "Недостаточно свободной RAM для модели и установленного резерва."
            )

    def _prompt_tokens(self, messages: list[dict[str, str]]) -> list[int]:
        prompt = self.template.render(messages)
        with self._tokenizer_lock:
            if self._closed:
                raise QueryError("model_closed", "Модель уже выгружена.")
            for message in messages:
                content = message["content"].encode("utf-8")
                parsed = self.model.tokenize(content, add_bos=False, special=True)
                literal = self.model.tokenize(content, add_bos=False, special=False)
                if parsed != literal:
                    raise QueryError(
                        "prompt_control_tokens",
                        "Вопрос или контекст содержит управляющие токены модели. "
                        "Удалите эти последовательности из текста.",
                    )
            tokens = self.model.tokenize(prompt.encode("utf-8"), add_bos=False, special=True)
        if self._add_bos and (not tokens or tokens[0] != self._bos_id):
            tokens.insert(0, self._bos_id)
        if not tokens or tokens[-1] == self._eos_id:
            raise QueryError(
                "chat_template_incompatible", "Шаблон не подготовил начало ответа ассистента."
            )
        return tokens

    def count_tokens(self, messages: list[dict[str, str]]) -> int:
        return len(self._prompt_tokens(messages))

    def _generate(self, messages: list[dict[str, str]], grammar: str, context: QueryContext) -> str:
        from llama_cpp import (
            LlamaGrammar,
            StoppingCriteriaList,
            ggml_abort_callback,
            llama_set_abort_callback,
        )

        context.check_cancelled()
        self._check_memory()
        deadline = time.monotonic() + self.timeout_seconds
        if context.deadline is not None:
            deadline = min(deadline, context.deadline)
        tokens = self._prompt_tokens(messages)
        if len(tokens) + self.max_tokens + 32 > self.context_size:
            raise QueryError(
                "context_limit",
                "Схема и вопрос не помещаются в контекст модели. Сузьте набор схем.",
            )

        last_memory_check = time.monotonic()
        memory_exhausted = False

        def should_abort():
            nonlocal last_memory_check, memory_exhausted
            now = time.monotonic()
            if now - last_memory_check >= 1:
                memory_exhausted = available_memory() < self.min_free_memory_mb * 1024 * 1024
                last_memory_check = now
            return memory_exhausted or context.cancelled.is_set() or now >= deadline

        def check_abort():
            context.check_cancelled()
            if memory_exhausted:
                raise QueryError(
                    "memory_limit",
                    "Во время генерации исчерпан резерв RAM. Выберите меньшую модель.",
                )
            if time.monotonic() >= deadline:
                raise QueryError("generation_timeout", "Превышено время генерации SQL.")

        def stop(input_tokens, _scores):
            return should_abort() or (
                len(input_tokens) > len(tokens) and input_tokens[-1] == self._eos_id
            )

        abort_callback = ggml_abort_callback(lambda _data: should_abort())
        llama_set_abort_callback(self.model.ctx, abort_callback, None)
        try:
            response = self.model.create_completion(
                prompt=tokens,
                grammar=LlamaGrammar.from_string(grammar, verbose=False),
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                stopping_criteria=StoppingCriteriaList([stop]),
            )
            check_abort()
        except BaseException:
            self.model.reset()
            check_abort()
            raise
        finally:
            llama_set_abort_callback(self.model.ctx, ggml_abort_callback(), None)
        choice = response["choices"][0]
        if choice.get("finish_reason") == "length":
            raise QueryError(
                "generation_truncated", "Модель не завершила SQL в пределах лимита токенов."
            )
        sql = choice["text"].strip()
        if not sql:
            raise QueryError("empty_generation", "Модель вернула пустой SQL.")
        return sql

    async def generate(
        self, messages: list[dict[str, str]], grammar: str, context: QueryContext
    ) -> str:
        async with self._gate:
            context.check_cancelled()
            return await finish_in_thread(
                self._generate,
                messages,
                grammar,
                context,
                on_cancel=context.cancelled.set,
            )

    def _close(self) -> None:
        with self._tokenizer_lock:
            if not self._closed:
                self._closed = True
                self.model.close()

    async def close(self) -> None:
        async with self._gate:
            await finish_in_thread(self._close)
