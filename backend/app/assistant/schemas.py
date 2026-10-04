from datetime import date

from pydantic import Field, model_validator

from backend.app.access.schemas import Input


class ConversationCreate(Input):
    title: str = Field(default="Новый вопрос", min_length=1, max_length=200)


class QuestionCreate(Input):
    question: str = Field(min_length=1, max_length=4000)
    source_id: str
    store_ids: list[str] = Field(default_factory=list, max_length=1000)
    base_run_id: str | None = None
    version: int = Field(ge=0)
    idempotency_key: str = Field(min_length=8, max_length=100)
    date_from: date | None = None
    date_to: date | None = None
    metric_id: str | None = None

    @model_validator(mode="after")
    def valid_period(self):
        if ("date_from" in self.model_fields_set) != ("date_to" in self.model_fields_set):
            raise ValueError("Both period bounds must be provided or omitted together")
        if bool(self.date_from) != bool(self.date_to):
            raise ValueError("Both period bounds are required")
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("Period start must precede end")
        return self
