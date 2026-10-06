from datetime import date

from pydantic import Field, model_validator

from backend.app.infrastructure.schemas import Input


class MeasurementPeriod(Input):
    date_from: date
    date_to: date

    @model_validator(mode="after")
    def valid_period(self):
        if self.date_from > self.date_to or (self.date_to - self.date_from).days > 730:
            raise ValueError("Choose a period no longer than two years")
        return self


class MeasurementCreate(MeasurementPeriod):
    idempotency_key: str = Field(min_length=8, max_length=100)


class MeasurementBasis(MeasurementPeriod):
    metric_id: str


class CaseCreate(Input):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10000)
    store_ids: list[str] = Field(min_length=1, max_length=1000)
    run_id: str | None = None
    assignee_id: str | None = None
    measurement: MeasurementBasis | None = None
    report_id: str | None = None
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=100)


class CommentCreate(Input):
    body: str = Field(min_length=1, max_length=10000)


class QuestionCreate(CommentCreate):
    assignee_id: str


class Answer(Input):
    answer: str = Field(min_length=1, max_length=10000)


class Close(Input):
    conclusion: str = Field(min_length=1, max_length=10000)
