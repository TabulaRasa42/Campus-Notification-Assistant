"""已校验的单事项提取结果与持久化待办。"""

from datetime import date as CalendarDate
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ValueModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CitedText(ValueModel):
    value: NonBlank
    evidence: str = ""
    origin: Literal["extracted", "user"] = "extracted"
    review: Literal["matched", "invalid", "missing"] = "matched"
    evidence_start: int | None = None
    evidence_end: int | None = None


class Material(CitedText):
    # One material or choice group. Its citation supports the complete requirement.
    # Empty for legacy records; never infer missing conditions on load.
    requirement: str = ""


class Deadline(ValueModel):
    date: CalendarDate | None = Field(default=None, strict=True)

    @field_validator("date", mode="before")
    @classmethod
    def date_only(cls, value: object) -> object:
        if isinstance(value, str):
            if len(value) != 10:
                raise ValueError("Date must contain only YYYY-MM-DD")
            return CalendarDate.fromisoformat(value)
        return value

    evidence: str = ""
    raw: str = ""
    status: Literal["certain", "uncertain", "missing"] = "certain"
    precision: Literal["date", "unknown"] = "date"
    origin: Literal["extracted", "user"] = "extracted"
    review: Literal["matched", "invalid", "missing"] = "matched"
    evidence_start: int | None = None
    evidence_end: int | None = None


class Item(ValueModel):
    action: CitedText
    deadline: Deadline = Field(
        default_factory=lambda: Deadline(status="missing", review="missing", precision="unknown")
    )
    materials: tuple[Material, ...] = ()

    @property
    def evidence_ready(self) -> bool:
        fields: tuple[CitedText | Deadline, ...] = (self.action, self.deadline, *self.materials)
        return all(field.review != "invalid" for field in fields)

    @field_validator("deadline", mode="before")
    @classmethod
    def missing_deadline(cls, value: object) -> object:
        return (
            {"status": "missing", "review": "missing", "precision": "unknown"}
            if value is None
            else value
        )


class Extraction(ValueModel):
    items: tuple[Item, ...]


class Draft(ValueModel):
    id: str
    source: str
    item: Item


class Todo(Draft):
    pass
