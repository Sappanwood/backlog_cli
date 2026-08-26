"""Unit tests for backlog items CRUD operations."""

import subprocess
from datetime import date, datetime
from pathlib import Path

import pytest

from backlog.items import (
    BacklogItemParseError,
    _apply_dependency_blocking,
    _jsonify,
    _load_item,
)
from backlog.items import (
    add_item as _add_item,
)
from backlog.items import (
    generate_index as _generate_index,
)
from backlog.items import (
    list_items as _list_items,
)
from backlog.items import (
    next_id as _next_id,
)
from backlog.items import (
    show_item as _show_item,
)
from backlog.items import (
    update_item as _update_item,
)
from backlog.models import (
    BacklogItem,
    Category,
    Priority,
    Status,
)
from backlog.store import load_store


def _store(root: Path):
    return load_store(root)


def add_item(item: BacklogItem, root: Path) -> Path:
    return _add_item(item, _store(root))


def generate_index(root: Path) -> str:
    return _generate_index(_store(root))


def list_items(root: Path) -> list[BacklogItem]:
    return _list_items(_store(root))


def show_item(item_id: str, root: Path) -> BacklogItem | None:
    return _show_item(item_id, _store(root))


def update_item(
    item_id: str, updates: dict, root: Path, expected_revision: str | None = None
) -> BacklogItem | None:
    return _update_item(item_id, updates, _store(root), expected_revision=expected_revision)


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
        "related_docs": [],
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
    """Create a valid exact store and return its items directory."""
    items_dir = tmp_path / "items"
    items_dir.mkdir(parents=True)
    (tmp_path / "INDEX.md").write_text("# Backlog Index\n", encoding="utf-8")
    (tmp_path / "backlog.json").write_text(
        '{"schema":"backlog/Store@1","project_id":"test","id_prefix":"TST"}\n',
        encoding="utf-8",
    )
    return items_dir


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

    def test_raises_error_for_invalid_file(self, tmp_path):
        items_dir = _make_tmp_backlog_dir(tmp_path)
        bad = items_dir / "bad.md"
        bad.write_text("not frontmatter")
        with pytest.raises(BacklogItemParseError):
            _load_item(bad)

    def test_date_fields_parsed(self, tmp_path):
        items_dir = _make_tmp_backlog_dir(tmp_path)
        _write_item(
            items_dir,
            "TST-001",
            created="2026-01-15",
            updated="2026-04-01",
            status="done",
            fixed_at="2026-03-15",
        )
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

    def test_fixed_at_cleared_when_not_done(self, tmp_path):
        items_dir = _make_tmp_backlog_dir(tmp_path)
        _write_item(items_dir, "TST-001", status="todo", fixed_at="2026-03-15")
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
        assert tst002.is_blocked is True
        assert tst002.effective_status == Status.BLOCKED
        assert tst002.status == Status.TODO


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
        assert item.is_blocked is True
        assert item.effective_status == Status.BLOCKED
        assert item.status == Status.TODO


class TestNextId:
    def test_first_id(self, tmp_path):
        items_dir = _make_tmp_backlog_dir(tmp_path)
        nid = _next_id(_store(tmp_path))
        assert nid == "TST-001"

    def test_increments(self, tmp_path):
        items_dir = _make_tmp_backlog_dir(tmp_path)
        _write_item(items_dir, "TST-001")
        _write_item(items_dir, "TST-002")
        nid = _next_id(_store(tmp_path))
        assert nid == "TST-003"


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
        _write_item(items_dir, "TST-002", project="test", title="Dep 1")
        item = BacklogItem(
            id="TST-001", project="test", title="Hello",
            category=Category.BUG, priority=Priority.P0,
            tags=["a", "b"], depends_on=["TST-002"],
            related_docs=["docs/ARCHITECTURE.md", "project-ops:research/topic.md"],
            body="## Body\nSome text.",
        )
        add_item(item, tmp_path)
        loaded = show_item("TST-001", tmp_path)
        assert loaded is not None
        assert loaded.title == "Hello"
        assert loaded.tags == ["a", "b"]
        assert loaded.depends_on == ["TST-002"]
        assert loaded.related_docs == ["docs/ARCHITECTURE.md", "project-ops:research/topic.md"]
        assert "## Body" in loaded.body


class TestUpdateItem:
    def test_updates_fields(self, tmp_path):
        items_dir = _make_tmp_backlog_dir(tmp_path)
        _write_item(items_dir, "TST-001", title="Old title", status="todo")
        result = update_item("TST-001", {"title": "New title", "status": Status.IN_PROGRESS}, tmp_path)
        assert result is not None
        assert result.title == "New title"
        assert result.status == Status.IN_PROGRESS

    def test_updates_related_docs(self, tmp_path):
        items_dir = _make_tmp_backlog_dir(tmp_path)
        _write_item(items_dir, "TST-001", related_docs=["docs/OLD.md"])
        result = update_item(
            "TST-001",
            {"related_docs": ["docs/ARCHITECTURE.md", "project-ops:plans/plan.md"]},
            tmp_path,
        )
        assert result is not None
        assert result.related_docs == ["docs/ARCHITECTURE.md", "project-ops:plans/plan.md"]

        loaded = show_item("TST-001", tmp_path)
        assert loaded is not None
        assert loaded.related_docs == ["docs/ARCHITECTURE.md", "project-ops:plans/plan.md"]

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

    def test_clears_fixed_at_when_reopened(self, tmp_path):
        items_dir = _make_tmp_backlog_dir(tmp_path)
        _write_item(items_dir, "TST-001", status="done", fixed_at="2026-03-15")
        result = update_item("TST-001", {"status": Status.TODO}, tmp_path)
        assert result is not None
        assert result.status == Status.TODO
        assert result.fixed_at is None
        assert "fixed_at" not in (items_dir / "TST-001.md").read_text()


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
        assert b.is_blocked is True
        assert b.status == Status.TODO

    def test_no_blocking_when_dep_done(self):
        items = [
            self._item("A", status=Status.DONE),
            self._item("B", depends_on=["A"]),
        ]
        _apply_dependency_blocking(items)
        b = next(i for i in items if i.id == "B")
        assert b.status == Status.TODO
        assert b.is_blocked is False

    def test_blocks_with_missing_dep(self):
        items = [self._item("B", depends_on=["MISSING"])]
        _apply_dependency_blocking(items)
        assert items[0].is_blocked is True
        assert items[0].status == Status.TODO

    def test_skips_done_items(self):
        items = [self._item("A", status=Status.DONE, depends_on=["B"])]
        _apply_dependency_blocking(items)
        assert items[0].status == Status.DONE
        assert items[0].is_blocked is False

    def test_skips_cancelled_items(self):
        items = [self._item("A", status=Status.CANCELLED, depends_on=["B"])]
        _apply_dependency_blocking(items)
        assert items[0].status == Status.CANCELLED
        assert items[0].is_blocked is False

    def test_cascading_block(self):
        items = [
            self._item("A"),
            self._item("B", depends_on=["A"]),
            self._item("C", depends_on=["B"]),
        ]
        _apply_dependency_blocking(items)
        b = next(i for i in items if i.id == "B")
        c = next(i for i in items if i.id == "C")
        assert b.is_blocked is True
        assert c.is_blocked is True
        assert b.status == Status.TODO
        assert c.status == Status.TODO


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

    def test_all_done_index_passes_git_diff_check(self, tmp_path):
        items_dir = _make_tmp_backlog_dir(tmp_path)
        _write_item(items_dir, "TST-001", status="done")

        content = generate_index(tmp_path)
        assert content.endswith("## Recommended Next (by score)\n")

        index_path = tmp_path / "INDEX.md"
        index_path.write_text(content)
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(["git", "add", index_path.name], cwd=tmp_path, check=True)
        result = subprocess.run(
            ["git", "diff", "--cached", "--check"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, result.stdout + result.stderr


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


class TestConflictPrevention:
    def test_add_item_conflict_raises_error(self, tmp_path):
        items_dir = _make_tmp_backlog_dir(tmp_path)
        item1 = BacklogItem(
            id="TST-001", project="test", title="First",
            category=Category.FEATURE, priority=Priority.P1,
        )
        add_item(item1, tmp_path)
        
        item2 = BacklogItem(
            id="TST-001", project="test", title="Second",
            category=Category.FEATURE, priority=Priority.P1,
        )
        with pytest.raises(FileExistsError, match="already exists"):
            add_item(item2, tmp_path)


class TestDecoupledBlockedStatus:
    def test_update_does_not_persist_blocked_status(self, tmp_path):
        items_dir = _make_tmp_backlog_dir(tmp_path)
        _write_item(items_dir, "TST-001", status="todo")
        _write_item(items_dir, "TST-002", status="todo", depends_on=["TST-001"])
        
        item = show_item("TST-002", tmp_path)
        assert item is not None
        assert item.is_blocked is True
        assert item.effective_status == Status.BLOCKED
        assert item.status == Status.TODO
        
        updated_item = update_item("TST-002", {"title": "New Title"}, tmp_path)
        assert updated_item is not None
        assert updated_item.title == "New Title"
        assert updated_item.status == Status.TODO
        assert updated_item.is_blocked is True
        assert updated_item.effective_status == Status.BLOCKED
        
        import frontmatter
        post = frontmatter.load(str(items_dir / "TST-002.md"))
        assert post.metadata.get("status") == "todo"
        assert "is_blocked" not in post.metadata
        assert "effective_status" not in post.metadata


class TestConcurrencyAndAtomicReplace:
    def test_atomic_replace_on_add_and_update(self, tmp_path):
        items_dir = _make_tmp_backlog_dir(tmp_path)
        item = BacklogItem(
            id="TST-001", project="test", title="Atomic",
            category=Category.FEATURE, priority=Priority.P1,
        )
        add_item(item, tmp_path)
        filepath = items_dir / "TST-001.md"
        assert filepath.exists()
        temp_filepath = filepath.with_suffix(".tmp")
        assert not temp_filepath.exists()
        
        update_item("TST-001", {"title": "Updated Atomic"}, tmp_path)
        assert filepath.exists()
        assert not temp_filepath.exists()
        updated = show_item("TST-001", tmp_path)
        assert updated is not None
        assert updated.title == "Updated Atomic"

    def test_add_writes_final_newline(self, tmp_path):
        items_dir = _make_tmp_backlog_dir(tmp_path)
        item = BacklogItem(
            id="TST-001", project="test", title="Final newline",
            category=Category.FEATURE, priority=Priority.P1,
            body="body without newline",
        )

        add_item(item, tmp_path)

        assert (items_dir / "TST-001.md").read_bytes().endswith(b"\n")

    def test_update_body_writes_final_newline(self, tmp_path):
        items_dir = _make_tmp_backlog_dir(tmp_path)
        item = BacklogItem(
            id="TST-001", project="test", title="Final newline",
            category=Category.FEATURE, priority=Priority.P1,
        )
        add_item(item, tmp_path)

        update_item("TST-001", {"body": "body from file without newline"}, tmp_path)

        assert (items_dir / "TST-001.md").read_bytes().endswith(b"\n")


class TestDependenciesCheck:
    def test_self_dependency_raises(self, tmp_path):
        import pytest
        _make_tmp_backlog_dir(tmp_path)
        item = BacklogItem(
            id="TST-001", project="test", title="Self dep",
            category=Category.FEATURE, priority=Priority.P1,
            depends_on=["TST-001"]
        )
        with pytest.raises(ValueError, match="Self dependency detected"):
            add_item(item, tmp_path)

    def test_missing_dependency_raises(self, tmp_path):
        import pytest
        _make_tmp_backlog_dir(tmp_path)
        item = BacklogItem(
            id="TST-002", project="test", title="Missing dep",
            category=Category.FEATURE, priority=Priority.P1,
            depends_on=["NONEXIST"]
        )
        with pytest.raises(ValueError, match="Dependency not found"):
            add_item(item, tmp_path)

    def test_circular_dependency_raises(self, tmp_path):
        import pytest
        _make_tmp_backlog_dir(tmp_path)
        # 先建立 TST-001
        item1 = BacklogItem(
            id="TST-001", project="test", title="Item 1",
            category=Category.FEATURE, priority=Priority.P1,
        )
        add_item(item1, tmp_path)
        
        # 建立 TST-002 依赖 TST-001
        item2 = BacklogItem(
            id="TST-002", project="test", title="Item 2",
            category=Category.FEATURE, priority=Priority.P1,
            depends_on=["TST-001"]
        )
        add_item(item2, tmp_path)

        # 现在更新 TST-001，让它依赖 TST-002，构成环
        with pytest.raises(ValueError, match="Circular dependency detected"):
            update_item("TST-001", {"depends_on": ["TST-002"]}, tmp_path)


class TestCustomFieldsExtra:
    def test_extra_fields_saved_and_loaded(self, tmp_path):
        items_dir = _make_tmp_backlog_dir(tmp_path)
        # 我们用已有的 _write_item 写一个包含自定义字段的文件
        _write_item(items_dir, "TST-001", project="test", foo="bar", hello="world")
        
        # 加载它，确认能读出 extra
        loaded = show_item("TST-001", tmp_path)
        assert loaded is not None
        assert loaded.extra == {"foo": "bar", "hello": "world"}
        
        # 写入一个新的带 extra 字段的项目
        item = BacklogItem(
            id="TST-002", project="test", title="Extra Field Item",
            category=Category.FEATURE, priority=Priority.P1,
            extra={"foo2": "bar2"}
        )
        add_item(item, tmp_path)
        
        # 确认物理上写入了文件且不带有 "extra: " 这样的嵌套 YAML
        filepath = items_dir / "TST-002.md"
        content = filepath.read_text()
        assert "foo2: bar2" in content
        assert "extra:" not in content


class TestRevisionAndLock:
    def test_item_revision_generated_on_add(self, tmp_path):
        _make_tmp_backlog_dir(tmp_path)
        item = BacklogItem(
            id="TST-001", project="test", title="Revision test",
            category=Category.FEATURE, priority=Priority.P1,
        )
        add_item(item, tmp_path)
        
        loaded = show_item("TST-001", tmp_path)
        assert loaded is not None
        assert loaded.revision != ""
        
        # update should change revision
        old_rev = loaded.revision
        updated = update_item("TST-001", {"title": "New title"}, tmp_path)
        assert updated is not None
        assert updated.revision != old_rev

    def test_optimistic_locking(self, tmp_path):
        import pytest
        _make_tmp_backlog_dir(tmp_path)
        item = BacklogItem(
            id="TST-001", project="test", title="Lock test",
            category=Category.FEATURE, priority=Priority.P1,
        )
        add_item(item, tmp_path)
        
        loaded = show_item("TST-001", tmp_path)
        assert loaded is not None
        
        # Correct expected revision
        update_item("TST-001", {"title": "Correct"}, tmp_path, expected_revision=loaded.revision)
        
        # Incorrect expected revision should raise error
        with pytest.raises(ValueError, match="Revision mismatch"):
            update_item("TST-001", {"title": "Incorrect"}, tmp_path, expected_revision="wrong-rev")

    def test_missing_revision_md5_fallback(self, tmp_path):
        items_dir = _make_tmp_backlog_dir(tmp_path)
        filepath = items_dir / "TST-001.md"
        filepath.write_text("""---
category: feature
created: '2026-04-27'
depends_on: []
effort: M
id: TST-001
impact: medium
priority: P2
project: test
status: todo
tags: []
title: test title
updated: '2026-06-07'
---
body text""")
        
        loaded = show_item("TST-001", tmp_path)
        assert loaded is not None
        assert loaded.revision != ""
        assert len(loaded.revision) == 8
        
        import hashlib
        expected_md5 = hashlib.md5(filepath.read_bytes()).hexdigest()[:8]
        assert loaded.revision == expected_md5
