from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from backend.app.access.schemas import Input


class StoreCreate(Input):
    name: str = Field(min_length=1, max_length=160)
    code: str = Field(min_length=1, max_length=120)
    city: str = Field(default="", max_length=160)
    owner_name: str = Field(default="", max_length=160)
    active: bool = True


class StoreUpdate(Input):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    city: str | None = Field(default=None, max_length=160)
    owner_name: str | None = Field(default=None, max_length=160)
    active: bool | None = None


class MetricCreate(Input):
    key: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=10, max_length=4000)
    unit: str = Field(default="RUB", min_length=1, max_length=24)
    source_id: str
    table_schema: str = Field(min_length=1, max_length=63)
    table_name: str = Field(min_length=1, max_length=63)
    value_column: str | None = None
    date_column: str = Field(min_length=1, max_length=63)
    store_column: str = Field(min_length=1, max_length=63)
    aggregation: Literal["sum", "count", "avg"]

    @model_validator(mode="after")
    def require_value(self):
        if self.aggregation != "count" and not self.value_column:
            raise ValueError("Value column is required")
        return self


class PlanCreate(Input):
    store_id: str
    metric_id: str
    period: date
    amount: Decimal = Field(ge=0, max_digits=20, decimal_places=4)

    @model_validator(mode="after")
    def month(self):
        if self.period.day != 1:
            raise ValueError("Plan period must be the first day of a month")
        return self


class ReportCreate(Input):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    run_id: str


class Refresh(Input):
    idempotency_key: str = Field(min_length=8, max_length=100)
