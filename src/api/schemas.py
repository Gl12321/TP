from typing import Literal

from pydantic import BaseModel, Field, field_validator


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    schemas_for_search: list[str] | Literal["all"] = "all"

    @field_validator("question")
    @classmethod
    def meaningful_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Вопрос не может быть пустым")
        return value

    @field_validator("schemas_for_search")
    @classmethod
    def schema_list(cls, value):
        if isinstance(value, list):
            if not value or len(value) > 100:
                raise ValueError("Выберите от 1 до 100 схем")
            if any(not item or len(item.encode("utf-8")) > 63 for item in value):
                raise ValueError("Некорректное название схемы")
            return list(dict.fromkeys(value))
        return value
