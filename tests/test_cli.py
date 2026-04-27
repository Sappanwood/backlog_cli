"""Unit tests for backlog CLI commands."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from backlog.cli import app


@pytest.fixture
def runner():
    return CliRunner()


def _make_backlog(tmp_path: Path) -> Path:
    """Create a docs/backlog/items/ dir and return the project path."""
    items_dir = tmp_path / "docs" / "backlog" / "items"
    items_dir.mkdir(parents=True)
    return tmp_path


def _write_item(items_dir: Path, item_id: str, **overrides):
    """Write a test markdown item file."""
    import yaml
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
    body = defaults.pop("body", "")
    f = items_dir / f"{item_id}.md"
    fm = yaml.dump(defaults, default_flow_style=False, sort_keys=False)
    f.write_text(f"---\n{fm}---\n{body}")


def _dir_flag(project_path: Path) -> list[str]:
    return ["--dir", str(project_path)]


class TestListCommand:
    def test_list_empty(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        result = runner.invoke(app, [*_dir_flag(project_path), "list"])
        assert result.exit_code == 0
        assert "No items found" in result.stdout

    def test_list_items(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        items_dir = project_path / "docs" / "backlog" / "items"
        _write_item(items_dir, "TST-001")
        _write_item(items_dir, "TST-002")
        result = runner.invoke(app, [*_dir_flag(project_path), "list"])
        assert result.exit_code == 0
        assert "TST-001" in result.stdout
        assert "TST-002" in result.stdout

    def test_list_filter_status(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        items_dir = project_path / "docs" / "backlog" / "items"
        _write_item(items_dir, "TST-001", status="done")
        _write_item(items_dir, "TST-002", status="todo")
        result = runner.invoke(app, [*_dir_flag(project_path), "list", "--status", "todo"])
        assert result.exit_code == 0
        assert "TST-002" in result.stdout
        assert "TST-001" not in result.stdout

    def test_list_filter_priority(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        items_dir = project_path / "docs" / "backlog" / "items"
        _write_item(items_dir, "TST-001", priority="P0")
        _write_item(items_dir, "TST-002", priority="P3")
        result = runner.invoke(app, [*_dir_flag(project_path), "list", "--priority", "P0"])
        assert result.exit_code == 0
        assert "TST-001" in result.stdout
        assert "TST-002" not in result.stdout

    def test_list_json_output(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        items_dir = project_path / "docs" / "backlog" / "items"
        _write_item(items_dir, "TST-001")
        result = runner.invoke(app, [*_dir_flag(project_path), "list", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert len(data) == 1
        assert data[0]["id"] == "TST-001"

    def test_list_sort_by_id(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        items_dir = project_path / "docs" / "backlog" / "items"
        _write_item(items_dir, "TST-002")
        _write_item(items_dir, "TST-001")
        result = runner.invoke(app, [*_dir_flag(project_path), "list", "--sort", "id"])
        assert result.exit_code == 0
        idx1 = result.stdout.index("TST-001")
        idx2 = result.stdout.index("TST-002")
        assert idx1 < idx2

    def test_list_limit(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        items_dir = project_path / "docs" / "backlog" / "items"
        _write_item(items_dir, "TST-001")
        _write_item(items_dir, "TST-002")
        _write_item(items_dir, "TST-003")
        result = runner.invoke(app, [*_dir_flag(project_path), "list", "--sort", "id", "--limit", "2"])
        assert result.exit_code == 0
        assert "Total: 2 items" in result.stdout

    def test_list_filter_tag(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        items_dir = project_path / "docs" / "backlog" / "items"
        _write_item(items_dir, "TST-001", tags=["urgent"])
        _write_item(items_dir, "TST-002", tags=["low"])
        result = runner.invoke(app, [*_dir_flag(project_path), "list", "--tag", "urgent"])
        assert result.exit_code == 0
        assert "TST-001" in result.stdout
        assert "TST-002" not in result.stdout


class TestShowCommand:
    def test_show_existing(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        items_dir = project_path / "docs" / "backlog" / "items"
        _write_item(items_dir, "TST-001", title="Hello World")
        result = runner.invoke(app, [*_dir_flag(project_path), "show", "TST-001"])
        assert result.exit_code == 0
        assert "Hello World" in result.stdout

    def test_show_missing(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        result = runner.invoke(app, [*_dir_flag(project_path), "show", "TST-999"])
        assert result.exit_code == 1
        assert "not found" in result.stdout

    def test_show_with_deps(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        items_dir = project_path / "docs" / "backlog" / "items"
        _write_item(items_dir, "TST-001", status="todo")
        _write_item(items_dir, "TST-002", status="todo", depends_on=["TST-001"])
        result = runner.invoke(app, [*_dir_flag(project_path), "show", "TST-002"])
        assert result.exit_code == 0
        assert "Depends on" in result.stdout
        assert "TST-001" in result.stdout


class TestAddCommand:
    def test_add_item(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        result = runner.invoke(app, [
            *(_dir_flag(project_path)),
            "add",
            "--project", "testing",
            "--title", "Test feature",
            "--category", "feature",
            "--priority", "P2",
        ])
        assert result.exit_code == 0
        assert "Created" in result.stdout
        assert "TES-001" in result.stdout

    def test_add_with_body(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        result = runner.invoke(app, [
            *(_dir_flag(project_path)),
            "add",
            "--project", "test",
            "--title", "With body",
            "--category", "bug",
            "--priority", "P1",
            "--body", "Some description",
        ])
        assert result.exit_code == 0
        # Verify body was stored
        show_result = runner.invoke(app, [*_dir_flag(project_path), "show", "TES-001"])
        assert "Some description" in show_result.stdout

    def test_add_with_deps(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        result = runner.invoke(app, [
            *(_dir_flag(project_path)),
            "add",
            "--project", "test",
            "--title", "Has deps",
            "--category", "feature",
            "--priority", "P1",
            "--depends-on", "A,B",
        ])
        assert result.exit_code == 0


class TestUpdateCommand:
    def test_update_title(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        items_dir = project_path / "docs" / "backlog" / "items"
        _write_item(items_dir, "TST-001")
        result = runner.invoke(app, [*_dir_flag(project_path), "update", "TST-001", "--title", "Updated"])
        assert result.exit_code == 0
        assert "Updated" in result.stdout

    def test_update_status(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        items_dir = project_path / "docs" / "backlog" / "items"
        _write_item(items_dir, "TST-001")
        result = runner.invoke(app, [*_dir_flag(project_path), "update", "TST-001", "--status", "in_progress"])
        assert result.exit_code == 0

    def test_update_fixed(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        items_dir = project_path / "docs" / "backlog" / "items"
        _write_item(items_dir, "TST-001")
        result = runner.invoke(app, [*_dir_flag(project_path), "update", "TST-001", "--fixed"])
        assert result.exit_code == 0
        show_result = runner.invoke(app, [*_dir_flag(project_path), "show", "TST-001"])
        assert "done" in show_result.stdout
        assert "Fixed at" in show_result.stdout

    def test_update_missing(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        result = runner.invoke(app, [*_dir_flag(project_path), "update", "TST-999", "--title", "X"])
        assert result.exit_code == 1

    def test_no_updates(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        items_dir = project_path / "docs" / "backlog" / "items"
        _write_item(items_dir, "TST-001")
        result = runner.invoke(app, [*_dir_flag(project_path), "update", "TST-001"])
        assert result.exit_code == 1


class TestStatsCommand:
    def test_stats_empty(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        result = runner.invoke(app, [*_dir_flag(project_path), "stats"])
        assert result.exit_code == 0
        assert "No items" in result.stdout

    def test_stats_with_items(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        items_dir = project_path / "docs" / "backlog" / "items"
        _write_item(items_dir, "TST-001", status="todo", priority="P1")
        _write_item(items_dir, "TST-002", status="done", priority="P2")
        result = runner.invoke(app, [*_dir_flag(project_path), "stats"])
        assert result.exit_code == 0
        assert "Total:" in result.stdout
        assert "Active:" in result.stdout


class TestNextCommand:
    def test_next_empty(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        result = runner.invoke(app, [*_dir_flag(project_path), "next"])
        assert result.exit_code == 0

    def test_next_with_items(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        items_dir = project_path / "docs" / "backlog" / "items"
        _write_item(items_dir, "TST-001", status="todo", priority="P1", impact="high", effort="XS")
        _write_item(items_dir, "TST-002", status="todo", priority="P3", impact="low", effort="XL")
        result = runner.invoke(app, [*_dir_flag(project_path), "next"])
        assert result.exit_code == 0
        assert "TST-001" in result.stdout

    def test_next_skips_blocked(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        items_dir = project_path / "docs" / "backlog" / "items"
        _write_item(items_dir, "TST-001", status="todo")
        _write_item(items_dir, "TST-002", status="todo", depends_on=["TST-001"])
        result = runner.invoke(app, [*_dir_flag(project_path), "next"])
        assert result.exit_code == 0
        # TST-002 should be blocked and not shown
        assert "TST-002" not in result.stdout


class TestIndexCommand:
    def test_index_generates(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        items_dir = project_path / "docs" / "backlog" / "items"
        _write_item(items_dir, "TST-001")
        result = runner.invoke(app, [*_dir_flag(project_path), "index"])
        assert result.exit_code == 0
        assert "Generated" in result.stdout
        index_path = project_path / "docs" / "backlog" / "INDEX.md"
        assert index_path.exists()
        content = index_path.read_text()
        assert "Backlog Index" in content


class TestEditCommand:
    def test_edit_missing(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        result = runner.invoke(app, [*_dir_flag(project_path), "edit", "TST-999"])
        assert result.exit_code == 1

    def test_edit_exists(self, runner, tmp_path, monkeypatch):
        project_path = _make_backlog(tmp_path)
        items_dir = project_path / "docs" / "backlog" / "items"
        _write_item(items_dir, "TST-001")
        monkeypatch.setenv("EDITOR", "cat")
        result = runner.invoke(app, [*_dir_flag(project_path), "edit", "TST-001"])
        assert result.exit_code == 0
