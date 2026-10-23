"""已校验的单事项提取结果与持久化待办。"""

from datetime import date as CalendarDate
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ValueModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CitedText(ValueModel):
    value: NonBlank
    evidence: NonBlank


class Deadline(ValueModel):
    date: CalendarDate = Field(strict=True)
    evidence: NonBlank


class Item(ValueModel):
    action: CitedText
    deadline: Deadline
    materials: tuple[CitedText, ...]


class Extraction(ValueModel):
    items: tuple[Item, ...]


class Draft(ValueModel):
    id: str
    source: str
    item: Item


class Todo(Draft):
    pass
