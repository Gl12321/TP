from collections.abc import Mapping
from datetime import datetime
import json

from sql_agent.query import QueryError


def _raise_template_error(message: str) -> None:
    raise ValueError(message)


def _to_json(value, ensure_ascii=False, indent=None, separators=None, sort_keys=False):
    return json.dumps(
        value, ensure_ascii=ensure_ascii, indent=indent, separators=separators, sort_keys=sort_keys
    )


class ChatTemplate:
    def __init__(self, source: str, *, bos_token: str, eos_token: str):
        from jinja2 import TemplateError
        from jinja2.ext import Extension, loopcontrols
        from jinja2.sandbox import ImmutableSandboxedEnvironment

        class GenerationBlock(Extension):
            tags = {"generation"}

            def parse(self, parser):
                next(parser.stream)
                return parser.parse_statements(("name:endgeneration",), drop_needle=True)

        if not isinstance(source, str) or not source.strip():
            raise QueryError("chat_template_missing", "В GGUF отсутствует шаблон диалога.")
        environment = ImmutableSandboxedEnvironment(
            trim_blocks=True,
            lstrip_blocks=True,
            extensions=[GenerationBlock, loopcontrols],
        )
        environment.filters["tojson"] = _to_json
        environment.globals.update(
            raise_exception=_raise_template_error,
            strftime_now=lambda pattern: datetime.now().strftime(pattern),
        )
        try:
            self._template = environment.from_string(source)
        except TemplateError as exc:
            raise QueryError(
                "chat_template_error", "Не удалось разобрать шаблон диалога GGUF."
            ) from exc
        self._template_error = TemplateError
        self.bos_token = bos_token
        self.eos_token = eos_token

    def render(self, messages: list[dict[str, str]]) -> str:
        if (
            not isinstance(messages, list)
            or not messages
            or any(
                not isinstance(message, Mapping)
                or not isinstance(message.get("role"), str)
                or message["role"] not in {"system", "user", "assistant"}
                or not isinstance(message.get("content"), str)
                for message in messages
            )
        ):
            raise QueryError("invalid_messages", "Для генерации нужны текстовые сообщения диалога.")
        try:
            prompt = self._template.render(
                messages=messages,
                bos_token=self.bos_token,
                eos_token=self.eos_token,
                add_generation_prompt=True,
                enable_thinking=False,
                tools=None,
                documents=None,
                functions=None,
                function_call=None,
                tool_choice=None,
            )
        except (self._template_error, ValueError, TypeError) as exc:
            raise QueryError(
                "chat_template_error", "Шаблон GGUF не поддерживает этот формат диалога."
            ) from exc
        if not prompt.strip():
            raise QueryError("chat_template_error", "Шаблон GGUF сформировал пустой промпт.")
        if prompt.rstrip().endswith("<think>"):
            raise QueryError(
                "chat_template_incompatible",
                "Шаблон модели оставляет открытый блок рассуждений при enable_thinking=False.",
            )
        return prompt
