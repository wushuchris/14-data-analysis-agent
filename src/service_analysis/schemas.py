"""Typed inputs for synthetic service-operations analysis."""
from datetime import date, datetime, timezone
from math import isfinite
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

Category = Literal["Routine", "Standard", "Complex"]
Region = Literal["North", "Central", "South"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def parse_date(value):
    if isinstance(value, datetime):
        raise ValueError("Expected a calendar date, not a timestamp")
    if isinstance(value, str):
        value = date.fromisoformat(value)
    if not isinstance(value, date):
        raise ValueError("Expected an ISO calendar date")
    return value


class Coverage(Contract):
    resolution_start: date
    resolution_end: date
    operations_start: date
    operations_end: date
    regions: tuple[Region, ...] = ("North", "Central", "South")

    @field_validator("resolution_start", "resolution_end", "operations_start", "operations_end", mode="before")
    @classmethod
    def dates(cls, value):
        return parse_date(value)

    @model_validator(mode="after")
    def bounds(self):
        if self.resolution_end < self.resolution_start or self.operations_end < self.operations_start:
            raise ValueError("Coverage end precedes start")
        if not self.regions or len(set(self.regions)) != len(self.regions):
            raise ValueError("Coverage requires unique regions")
        if (self.operations_end - self.operations_start).days > 366:
            raise ValueError("Operations coverage exceeds one year")
        return self


class CompletedRequest(Contract):
    request_id: str = Field(min_length=1, max_length=80, strict=True)
    opened_at: datetime
    resolved_at: datetime
    category: Category | None = None
    region: Region | None = None

    @field_validator("request_id")
    @classmethod
    def identifier(cls, value):
        if not value.strip() or value != value.strip():
            raise ValueError("Identifier must be nonblank without surrounding whitespace")
        return value

    @field_validator("opened_at", "resolved_at", mode="before")
    @classmethod
    def timestamp(cls, value):
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Expected a timezone-aware ISO timestamp")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def chronological(self):
        if self.resolved_at < self.opened_at:
            raise ValueError("Resolution precedes opening")
        return self

    @property
    def resolution_hours(self) -> float:
        return (self.resolved_at - self.opened_at).total_seconds() / 3600


class DailyOperations(Contract):
    date: date
    region: Region
    incoming_requests: StrictInt = Field(ge=0)
    staffed_hours: float | None = None

    @field_validator("date", mode="before")
    @classmethod
    def calendar_date(cls, value):
        return parse_date(value)

    @field_validator("staffed_hours", mode="before")
    @classmethod
    def staffing(cls, value):
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Staffed hours must be numeric")
        if not isfinite(value) or value < 0:
            raise ValueError("Staffed hours must be finite and nonnegative")
        return float(value)


class QualityPolicy(Contract):
    tiny_group_threshold: StrictInt = Field(default=5, ge=1)
    max_rows_per_table: StrictInt = Field(default=10000, ge=1, le=100000)
