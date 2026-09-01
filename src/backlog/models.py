"""Backlog data models."""

import re
from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator


class Priority(StrEnum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class Effort(StrEnum):
    XS = "XS"
    S = "S"
    M = "M"
    L = "L"
    XL = "XL"


class Impact(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Status(StrEnum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"


class Category(StrEnum):
    BUG = "bug"
    A11Y = "a11y"
    UX = "ux"
    I18N = "i18n"
    TESTING = "testing"
    FEATURE = "feature"
    REFACTOR = "refactor"
    PERF = "perf"
    DOCS = "docs"
    ARCHITECTURE = "architecture"
    SECURITY = "security"
    RESEARCH = "research"
    OPS = "ops"


class ItemType(StrEnum):
    TASK = "task"
    EPIC = "epic"


PRIORITY_WEIGHT: dict[Priority, int] = {
    Priority.P0: 100,
    Priority.P1: 50,
    Priority.P2: 10,
    Priority.P3: 1,
}

EFFORT_WEIGHT: dict[Effort, float] = {
    Effort.XS: 10.0,
    Effort.S: 5.0,
    Effort.M: 2.0,
    Effort.L: 1.0,
    Effort.XL: 0.5,
}

IMPACT_WEIGHT: dict[Impact, int] = {
    Impact.HIGH: 3,
    Impact.MEDIUM: 2,
    Impact.LOW: 1,
}


class BacklogItem(BaseModel):
    id: str
    project: str
    title: str
    item_type: ItemType = ItemType.TASK
    parent_id: str | None = None
    category: Category
    priority: Priority
    effort: Effort = Effort.M
    impact: Impact = Impact.MEDIUM
    status: Status = Status.TODO
    source: str = ""
    fixed_at: date | None = None
    tags: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    related_docs: list[str] = Field(default_factory=list)
    created: date = Field(default_factory=lambda: date.today())
    updated: date = Field(default_factory=lambda: date.today())
    body: str = ""
    is_blocked: bool = Field(default=False, exclude=True)
    revision: str = Field(default="")
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        if v == "AUTO":
            return v
        if not re.match(r"^[a-zA-Z0-9_-]+$", v):
            raise ValueError("ID can only contain alphanumeric characters, hyphens, and underscores")
        return v

    @field_validator("project")
    @classmethod
    def validate_project(cls, v: str) -> str:
        if not re.match(r"^[a-zA-Z0-9_-]+$", v):
            raise ValueError("Project name can only contain alphanumeric characters, hyphens, and underscores")
        return v

    @field_validator("parent_id")
    @classmethod
    def validate_parent_id(cls, v: str | None) -> str | None:
        if v is not None and not re.fullmatch(r"[a-zA-Z0-9_-]+", v):
            raise ValueError("Parent ID can only contain alphanumeric characters, hyphens, and underscores")
        return v

    @model_validator(mode="after")
    def clear_fixed_at_unless_done(self) -> "BacklogItem":
        if self.item_type == ItemType.EPIC and self.parent_id is not None:
            raise ValueError("Epic items cannot have a parent")
        if self.parent_id == self.id:
            raise ValueError(f"Item '{self.id}' cannot be its own parent")
        if self.status != Status.DONE:
            self.fixed_at = None
        return self

    @computed_field
    @property
    def effective_status(self) -> Status:
        if self.is_blocked:
            return Status.BLOCKED
        return self.status

    @computed_field
    @property
    def score(self) -> float:
        if self.effective_status in (Status.DONE, Status.CANCELLED):
            return 0.0
        return (
            PRIORITY_WEIGHT[self.priority]
            * IMPACT_WEIGHT[self.impact]
            * EFFORT_WEIGHT[self.effort]
        )
