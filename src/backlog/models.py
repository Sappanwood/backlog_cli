"""Backlog data models."""

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field, computed_field, model_validator


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
    category: Category
    priority: Priority
    effort: Effort = Effort.M
    impact: Impact = Impact.MEDIUM
    status: Status = Status.TODO
    source: str = ""
    fixed_at: date | None = None
    tags: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    created: date = Field(default_factory=lambda: date.today())
    updated: date = Field(default_factory=lambda: date.today())
    body: str = ""

    @model_validator(mode="after")
    def clear_fixed_at_unless_done(self) -> "BacklogItem":
        if self.status != Status.DONE:
            self.fixed_at = None
        return self

    @computed_field
    @property
    def score(self) -> float:
        if self.status in (Status.DONE, Status.CANCELLED):
            return 0.0
        return (
            PRIORITY_WEIGHT[self.priority]
            * IMPACT_WEIGHT[self.impact]
            * EFFORT_WEIGHT[self.effort]
        )
