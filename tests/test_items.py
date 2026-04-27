"""Unit tests for backlog items CRUD operations."""

from datetime import date, datetime
from pathlib import Path

from backlog.items import (
    _apply_dependency_blocking,
    _find_backlog_dir,
    _jsonify,
    _load_item,
    add_item,
    generate_index,
    get_items_dir,
    list_items,
    next_id,
    show_item,
    update_item,
)
from backlog.models import (
    BacklogItem,
    Category,
    Priority,
    Status,
)


def _write_item(items_dir: Path, item_id: str, **overrides) -> Path:
    """Write a test markdown item file and return its path."""
    defaults = {
        "id": item_id,
        "project": "test",
        "title": f"Item {item_id}",
        "category": "feature",
        "priority": "P2",
        "effort": "M",
        "impact": "medium",
        "status": "todo",
        "source": "",
        "tags": [],
        "depends_on": [],
        "created": "2026-04-27",
        "updated": "2026-04-27",
    }
    defaults.update(overrides)
    defaults.pop("body", None)
    body = overrides.get("body", "")

    lines = ["---"]
    for key, value in defaults.items():
        if isinstance(value, list):
            lines.append(f"{key}: {value}")
        elif isinstance(value, str):
            lines.append(f'{key}: "{value}"')
        else:
            lines.append(f"{key}: {value}")
    # Handle special list fields properly
    import yaml
    lines = ["---"]
    yaml_dict = {}
    for key, value in defaults.items():
        yaml_dict[key] = value
    lines = yaml.dump(yaml_dict, default_flow_style=False, sort_keys=False).strip().split("\n")
    frontmatter_block = "---\n" + "\n".join(lines) + "\n---\n"
    content = frontmatter_block + body
    filepath = items_dir / f"{item_id}.md"
    filepath.write_text(content)
    return filepath


def _make_tmp_backlog_dir(tmp_path: Path) -> Path:
    """Create a docs/backlog/items/ directory inside tmp_path."""
    base = tmp_path / "docs" / "backlog"
    items_dir = base / "items"
    items_dir.mkdir(parents=True)
    return items_dir


class TestFindBacklogDir:
    def test_finds_existing(self, tmp_path):
        items_dir = _make_tmp_backlog_dir(tmp_path)
        result = _find_backlog_dir(tmp_path)
        assert result is not None
        assert result.name == "backlog"

    def test_finds_from_subdir(self, tmp_path):
        items_dir = _make_tmp_backlog_dir(tmp_path)
        subdir = tmp_path / "src" / "deep"
        subdir.mkdir(parents=True)
        result = _find_backlog_dir(subdir)
        assert result is not None
        assert result.name == "backlog"

    def test_returns_none_when_not_found(self, tmp_path):
        result = _find_backlog_dir(tmp_path)
        assert result is None


class TestGetItemsDir:
    def test_creates_when_missing(self, tmp_path):
        items_dir = get_items_dir(tmp_path)
        assert items_dir.exists()
        assert items_dir.name == "items"

    def test_reuses_existing(self, tmp_path):
        items_dir = _make_tmp_backlog_dir(tmp_path)
        result = get_items_dir(tmp_path)
        assert result == items_dir


class TestLoadItem:
    def test_loads_valid_file(self, tmp_path):
        items_dir = _make_tmp_backlog_dir(tmp_path)
        _write_item(items_dir, "TST-001")
        filepath = items_dir / "TST-001.md"
        item = _load_item(filepath)
        assert item is not None
        assert item.id == "TST-001"
        assert item.title == "Item TST-001"

    def test_loads_with_body(self, tmp_path):
        items_dir = _make_tmp_backlog_dir(tmp_path)
        _write_item(items_dir, "TST-001", body="Hello world")
        filepath = items_dir / "TST-001.md"
        item = _load_item(filepath)
        assert item is not None
        assert item.body.strip() == "Hello world"

    def test_returns_none_for_invalid_file(self, tmp_path):
        items_dir = _make_tmp_backlog_dir(tmp_path)
        bad = items_dir / "bad.md"
        bad.write_text("not frontmatter")
        assert _load_item(bad) is None

    def test_date_fields_parsed(self, tmp_path):
        items_dir = _make_tmp_backlog_dir(tmp_path)
        _write_item(items_dir, "TST-001", created="2026-01-15", updated="2026-04-01", fixed_at="2026-03-15")
        filepath = items_dir / "TST-001.md"
        item = _load_item(filepath)
        assert item is not None
        assert item.created == date(2026, 1, 15)
        assert item.updated == date(2026, 4, 1)
        assert item.fixed_at == date(2026, 3, 15)

    def test_fixed_at_none_when_not_set(self, tmp_path):
        items_dir = _make_tmp_backlog_dir(tmp_path)
        _write_item(items_dir, "TST-001", fixed_at=None)
        filepath = items_dir / "TST-001.md"
        item = _load_item(filepath)
        assert item is not None
        assert item.fixed_at is None


class TestListItems:
    def test_returns_empty_for_empty_dir(self, tmp_path):
        items_dir = _make_tmp_backlog_dir(tmp_path)
        items = list_items(tmp_path)
        assert items == []

    def test_returns_items_sorted(self, tmp_path):
        items_dir = _make_tmp_backlog_dir(tmp_path)
        _write_item(items_dir, "TST-002")
        _write_item(items_dir, "TST-001")
        items = list_items(tmp_path)
        assert len(items) == 2
        assert items[0].id == "TST-001"
        assert items[1].id == "TST-002"

    def test_applies_dependency_blocking(self, tmp_path):
        items_dir = _make_tmp_backlog_dir(tmp_path)
        _write_item(items_dir, "TST-001", status="todo")
        _write_item(items_dir, "TST-002", status="todo", depends_on=["TST-001"])
        items = list_items(tmp_path)
        tst002 = next(i for i in items if i.id == "TST-002")
        assert tst002.status == Status.BLOCKED


class TestShowItem:
    def test_returns_none_for_missing(self, tmp_path):
        items_dir = _make_tmp_backlog_dir(tmp_path)
        assert show_item("TST-999", tmp_path) is None

    def test_shows_existing(self, tmp_path):
        items_dir = _make_tmp_backlog_dir(tmp_path)
        _write_item(items_dir, "TST-001", title="Show me")
        item = show_item("TST-001", tmp_path)
        assert item is not None
        assert item.title == "Show me"

    def test_shows_blocked_by_deps(self, tmp_path):
        items_dir = _make_tmp_backlog_dir(tmp_path)
        _write_item(items_dir, "TST-001", status="todo")
        _write_item(items_dir, "TST-002", status="todo", depends_on=["TST-001"])
        item = show_item("TST-002", tmp_path)
        assert item is not None
        assert item.status == Status.BLOCKED


class TestNextId:
    def test_first_id(self, tmp_path):
        items_dir = _make_tmp_backlog_dir(tmp_path)
        nid = next_id("testing", tmp_path)
        assert nid == "TES-001"

    def test_increments(self, tmp_path):
        items_dir = _make_tmp_backlog_dir(tmp_path)
        _write_item(items_dir, "TES-001", project="testing", id="TES-001")
        _write_item(items_dir, "TES-002", project="testing", id="TES-002")
        nid = next_id("testing", tmp_path)
        assert nid == "TES-003"

    def test_ignores_other_projects(self, tmp_path):
        items_dir = _make_tmp_backlog_dir(tmp_path)
        _write_item(items_dir, "OTH-001", project="other", id="OTH-001")
        nid = next_id("testing", tmp_path)
        assert nid == "TES-001"


class TestAddItem:
    def test_creates_file(self, tmp_path):
        items_dir = _make_tmp_backlog_dir(tmp_path)
        item = BacklogItem(
            id="TST-001", project="test", title="New item",
            category=Category.FEATURE, priority=Priority.P1,
        )
        filepath = add_item(item, tmp_path)
        assert filepath.exists()
        assert filepath.name == "TST-001.md"

    def test_content_is_valid(self, tmp_path):
        items_dir = _make_tmp_backlog_dir(tmp_path)
        item = BacklogItem(
            id="TST-001", project="test", title="Hello",
            category=Category.BUG, priority=Priority.P0,
            tags=["a", "b"], depends_on=["DEP-001"],
            body="## Body\nSome text.",
        )
        add_item(item, tmp_path)
        loaded = show_item("TST-001", tmp_path)
        assert loaded is not None
        assert loaded.title == "Hello"
        assert loaded.tags == ["a", "b"]
        assert loaded.depends_on == ["DEP-001"]
        assert "## Body" in loaded.body


class TestUpdateItem:
    def test_updates_fields(self, tmp_path):
        items_dir = _make_tmp_backlog_dir(tmp_path)
        _write_item(items_dir, "TST-001", title="Old title", status="todo")
        result = update_item("TST-001", {"title": "New title", "status": Status.IN_PROGRESS}, tmp_path)
        assert result is not None
        assert result.title == "New title"
        assert result.status == Status.IN_PROGRESS

    def test_preserves_body(self, tmp_path):
        items_dir = _make_tmp_backlog_dir(tmp_path)
        _write_item(items_dir, "TST-001", body="Original body")
        result = update_item("TST-001", {"title": "Updated"}, tmp_path)
        assert result is not None
        assert "Original body" in result.body

    def test_returns_none_for_missing(self, tmp_path):
        items_dir = _make_tmp_backlog_dir(tmp_path)
        assert update_item("TST-999", {"title": "X"}, tmp_path) is None

    def test_updates_updated_date(self, tmp_path):
        items_dir = _make_tmp_backlog_dir(tmp_path)
        _write_item(items_dir, "TST-001", updated="2026-01-01")
        result = update_item("TST-001", {"title": "X"}, tmp_path)
        assert result is not None
        assert result.updated == date.today()


class TestApplyDependencyBlocking:
    def _item(self, id, **kw):
        defaults = {
            "project": "test", "title": id, "category": Category.FEATURE,
            "priority": Priority.P1, "status": Status.TODO,
        }
        defaults.update(kw)
        return BacklogItem(id=id, **defaults)

    def test_no_blocking_without_deps(self):
        items = [self._item("A")]
        _apply_dependency_blocking(items)
        assert items[0].status == Status.TODO

    def test_blocks_when_dep_not_done(self):
        items = [
            self._item("A"),
            self._item("B", depends_on=["A"]),
        ]
        _apply_dependency_blocking(items)
        b = next(i for i in items if i.id == "B")
        assert b.status == Status.BLOCKED

    def test_no_blocking_when_dep_done(self):
        items = [
            self._item("A", status=Status.DONE),
            self._item("B", depends_on=["A"]),
        ]
        _apply_dependency_blocking(items)
        b = next(i for i in items if i.id == "B")
        assert b.status == Status.TODO

    def test_blocks_with_missing_dep(self):
        items = [self._item("B", depends_on=["MISSING"])]
        _apply_dependency_blocking(items)
        assert items[0].status == Status.BLOCKED

    def test_skips_done_items(self):
        items = [self._item("A", status=Status.DONE, depends_on=["B"])]
        _apply_dependency_blocking(items)
        assert items[0].status == Status.DONE

    def test_skips_cancelled_items(self):
        items = [self._item("A", status=Status.CANCELLED, depends_on=["B"])]
        _apply_dependency_blocking(items)
        assert items[0].status == Status.CANCELLED

    def test_cascading_block(self):
        items = [
            self._item("A"),
            self._item("B", depends_on=["A"]),
            self._item("C", depends_on=["B"]),
        ]
        _apply_dependency_blocking(items)
        b = next(i for i in items if i.id == "B")
        c = next(i for i in items if i.id == "C")
        assert b.status == Status.BLOCKED
        assert c.status == Status.BLOCKED


class TestGenerateIndex:
    def test_empty_index(self, tmp_path):
        items_dir = _make_tmp_backlog_dir(tmp_path)
        content = generate_index(tmp_path)
        assert "# Backlog Index" in content
        assert "Total items: 0" in content

    def test_index_with_items(self, tmp_path):
        items_dir = _make_tmp_backlog_dir(tmp_path)
        _write_item(items_dir, "TST-001", status="todo", priority="P1", category="feature")
        _write_item(items_dir, "TST-002", status="done", priority="P3", category="bug")
        content = generate_index(tmp_path)
        assert "Total items: 2" in content
        assert "todo" in content
        assert "done" in content
        assert "P1" in content
        assert "P3" in content
        assert "feature" in content
        assert "TST-001" in content


class TestJsonify:
    def test_enum_to_value(self):
        assert _jsonify(Status.DONE) == "done"
        assert _jsonify(Priority.P0) == "P0"

    def test_date_to_isoformat(self):
        d = date(2026, 4, 27)
        assert _jsonify(d) == "2026-04-27"

    def test_datetime_to_isoformat(self):
        dt = datetime(2026, 4, 27, 12, 0, 0)
        assert _jsonify(dt) == "2026-04-27T12:00:00"

    def test_list_recursive(self):
        assert _jsonify([Status.TODO, Priority.P1]) == ["todo", "P1"]

    def test_passthrough(self):
        assert _jsonify("hello") == "hello"
        assert _jsonify(42) == 42
        assert _jsonify(None) is None
