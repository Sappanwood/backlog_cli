"""Unit tests for backlog models."""

from datetime import date

import pytest
from pydantic import ValidationError

from backlog.models import (
    EFFORT_WEIGHT,
    IMPACT_WEIGHT,
    PRIORITY_WEIGHT,
    BacklogItem,
    Category,
    Effort,
    Impact,
    Priority,
    Status,
)


class TestEnums:
    def test_priority_values(self):
        assert Priority.P0.value == "P0"
        assert Priority.P1.value == "P1"
        assert Priority.P2.value == "P2"
        assert Priority.P3.value == "P3"

    def test_status_values(self):
        assert Status.TODO.value == "todo"
        assert Status.IN_PROGRESS.value == "in_progress"
        assert Status.DONE.value == "done"
        assert Status.CANCELLED.value == "cancelled"
        assert Status.BLOCKED.value == "blocked"

    def test_effort_values(self):
        assert Effort.XS.value == "XS"
        assert Effort.S.value == "S"
        assert Effort.M.value == "M"
        assert Effort.L.value == "L"
        assert Effort.XL.value == "XL"

    def test_impact_values(self):
        assert Impact.HIGH.value == "high"
        assert Impact.MEDIUM.value == "medium"
        assert Impact.LOW.value == "low"

    def test_category_values(self):
        assert Category.BUG.value == "bug"
        assert Category.FEATURE.value == "feature"
        assert Category.OPS.value == "ops"
        assert Category.TESTING.value == "testing"


class TestWeights:
    def test_priority_weights(self):
        assert PRIORITY_WEIGHT[Priority.P0] == 100
        assert PRIORITY_WEIGHT[Priority.P1] == 50
        assert PRIORITY_WEIGHT[Priority.P2] == 10
        assert PRIORITY_WEIGHT[Priority.P3] == 1
        assert len(PRIORITY_WEIGHT) == 4

    def test_effort_weights(self):
        assert EFFORT_WEIGHT[Effort.XS] == 10.0
        assert EFFORT_WEIGHT[Effort.S] == 5.0
        assert EFFORT_WEIGHT[Effort.M] == 2.0
        assert EFFORT_WEIGHT[Effort.L] == 1.0
        assert EFFORT_WEIGHT[Effort.XL] == 0.5

    def test_impact_weights(self):
        assert IMPACT_WEIGHT[Impact.HIGH] == 3
        assert IMPACT_WEIGHT[Impact.MEDIUM] == 2
        assert IMPACT_WEIGHT[Impact.LOW] == 1


class TestBacklogItem:
    def test_create_minimal(self):
        item = BacklogItem(
            id="TST-001",
            project="test",
            title="Test item",
            category=Category.TESTING,
            priority=Priority.P3,
        )
        assert item.id == "TST-001"
        assert item.project == "test"
        assert item.title == "Test item"
        assert item.category == Category.TESTING
        assert item.priority == Priority.P3
        assert isinstance(item.created, date)

    def test_default_values(self):
        item = BacklogItem(
            id="TST-002",
            project="test",
            title="Defaults test",
            category=Category.FEATURE,
            priority=Priority.P2,
        )
        assert item.effort == Effort.M
        assert item.impact == Impact.MEDIUM
        assert item.status == Status.TODO
        assert item.source == ""
        assert item.fixed_at is None
        assert item.tags == []
        assert item.depends_on == []
        assert item.body == ""

    def test_all_fields(self):
        today = date.today()
        item = BacklogItem(
            id="TST-003",
            project="test",
            title="Full item",
            category=Category.BUG,
            priority=Priority.P0,
            effort=Effort.XS,
            impact=Impact.HIGH,
            status=Status.DONE,
            source="github",
            fixed_at=today,
            tags=["urgent", "frontend"],
            depends_on=["TST-001"],
            created=today,
            updated=today,
            body="Some markdown body.",
        )
        assert item.effort == Effort.XS
        assert item.impact == Impact.HIGH
        assert item.status == Status.DONE
        assert item.source == "github"
        assert item.fixed_at == today
        assert item.tags == ["urgent", "frontend"]
        assert item.depends_on == ["TST-001"]
        assert item.body == "Some markdown body."

    def test_missing_required_raises(self):
        with pytest.raises(ValidationError):
            BacklogItem(id="X", project="x", category=Category.BUG, priority=Priority.P1)  # pyright: ignore[reportCallIssue]

        with pytest.raises(ValidationError):
            BacklogItem(id="X", project="x", title="x", priority=Priority.P1)  # pyright: ignore[reportCallIssue]

    def test_invalid_enum_raises(self):
        with pytest.raises(ValidationError):
            BacklogItem(
                id="X", project="x", title="x",
                category="not_a_category", priority=Priority.P1,  # pyright: ignore[reportArgumentType]
            )

    def test_fixed_at_cleared_unless_done(self):
        item = BacklogItem(
            id="TST-004",
            project="test",
            title="Reopened item",
            category=Category.BUG,
            priority=Priority.P1,
            status=Status.IN_PROGRESS,
            fixed_at=date.today(),
        )
        assert item.fixed_at is None


class TestScore:
    def test_score_todo(self):
        item = BacklogItem(
            id="TST-001", project="test", title="Score test",
            category=Category.FEATURE, priority=Priority.P1,
            impact=Impact.HIGH, effort=Effort.S,
        )
        # P1=50, HIGH=3, S=5 → 50*3*5 = 750
        assert item.score == 750.0

    def test_score_p3_low_large(self):
        item = BacklogItem(
            id="TST-001", project="test", title="Low",
            category=Category.UX, priority=Priority.P3,
            impact=Impact.LOW, effort=Effort.L,
        )
        # P3=1, LOW=1, L=1 → 1
        assert item.score == 1.0

    def test_score_done_is_zero(self):
        item = BacklogItem(
            id="TST-001", project="test", title="Done",
            category=Category.FEATURE, priority=Priority.P0,
            impact=Impact.HIGH, effort=Effort.XS, status=Status.DONE,
        )
        assert item.score == 0.0

    def test_score_cancelled_is_zero(self):
        item = BacklogItem(
            id="TST-001", project="test", title="Cancelled",
            category=Category.FEATURE, priority=Priority.P0,
            impact=Impact.HIGH, effort=Effort.XS, status=Status.CANCELLED,
        )
        assert item.score == 0.0

    def test_score_blocked_positive(self):
        item = BacklogItem(
            id="TST-001", project="test", title="Blocked",
            category=Category.FEATURE, priority=Priority.P1,
            impact=Impact.MEDIUM, effort=Effort.M, status=Status.BLOCKED,
        )
        # P1=50, MEDIUM=2, M=2 → 200
        assert item.score == 200.0
