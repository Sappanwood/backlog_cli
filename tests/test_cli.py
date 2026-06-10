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


def _target_flag(project_path: Path) -> list[str]:
    return ["--target", str(project_path)]


class TestListCommand:
    def test_list_empty(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        result = runner.invoke(app, [*_target_flag(project_path), "list"])
        assert result.exit_code == 0
        assert "No items found" in result.stdout

    def test_list_items(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        items_dir = project_path / "docs" / "backlog" / "items"
        _write_item(items_dir, "TST-001")
        _write_item(items_dir, "TST-002")
        result = runner.invoke(app, [*_target_flag(project_path), "list"])
        assert result.exit_code == 0
        assert "TST-001" in result.stdout
        assert "TST-002" in result.stdout

    def test_list_filter_status(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        items_dir = project_path / "docs" / "backlog" / "items"
        _write_item(items_dir, "TST-001", status="done")
        _write_item(items_dir, "TST-002", status="todo")
        result = runner.invoke(app, [*_target_flag(project_path), "list", "--status", "todo"])
        assert result.exit_code == 0
        assert "TST-002" in result.stdout
        assert "TST-001" not in result.stdout

    def test_list_filter_priority(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        items_dir = project_path / "docs" / "backlog" / "items"
        _write_item(items_dir, "TST-001", priority="P0")
        _write_item(items_dir, "TST-002", priority="P3")
        result = runner.invoke(app, [*_target_flag(project_path), "list", "--priority", "P0"])
        assert result.exit_code == 0
        assert "TST-001" in result.stdout
        assert "TST-002" not in result.stdout

    def test_list_json_output(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        items_dir = project_path / "docs" / "backlog" / "items"
        _write_item(items_dir, "TST-001")
        result = runner.invoke(app, [*_target_flag(project_path), "list", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["ok"] is True
        assert len(data["data"]) == 1
        assert data["data"][0]["id"] == "TST-001"
        assert data["data"][0]["score"] == 40.0

    def test_list_sort_by_id(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        items_dir = project_path / "docs" / "backlog" / "items"
        _write_item(items_dir, "TST-002")
        _write_item(items_dir, "TST-001")
        result = runner.invoke(app, [*_target_flag(project_path), "list", "--sort", "id"])
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
        result = runner.invoke(app, [*_target_flag(project_path), "list", "--sort", "id", "--limit", "2"])
        assert result.exit_code == 0
        assert "Total: 2 items" in result.stdout

    def test_list_filter_tag(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        items_dir = project_path / "docs" / "backlog" / "items"
        _write_item(items_dir, "TST-001", tags=["urgent"])
        _write_item(items_dir, "TST-002", tags=["low"])
        result = runner.invoke(app, [*_target_flag(project_path), "list", "--tag", "urgent"])
        assert result.exit_code == 0
        assert "TST-001" in result.stdout
        assert "TST-002" not in result.stdout


class TestShowCommand:
    def test_show_existing(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        items_dir = project_path / "docs" / "backlog" / "items"
        _write_item(items_dir, "TST-001", title="Hello World")
        result = runner.invoke(app, [*_target_flag(project_path), "show", "TST-001"])
        assert result.exit_code == 0
        assert "Hello World" in result.stdout

    def test_show_missing(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        result = runner.invoke(app, [*_target_flag(project_path), "show", "TST-999"])
        assert result.exit_code == 1
        assert "not found" in result.stdout

    def test_show_with_deps(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        items_dir = project_path / "docs" / "backlog" / "items"
        _write_item(items_dir, "TST-001", status="todo")
        _write_item(items_dir, "TST-002", status="todo", depends_on=["TST-001"])
        result = runner.invoke(app, [*_target_flag(project_path), "show", "TST-002"])
        assert result.exit_code == 0
        assert "Depends on" in result.stdout
        assert "TST-001" in result.stdout


class TestAddCommand:
    def test_add_item(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        result = runner.invoke(app, [
            *(_target_flag(project_path)),
            "add",
            "--title", "Test feature",
            "--category", "feature",
            "--priority", "P2",
        ])
        assert result.exit_code == 0
        assert "Created" in result.stdout

    def test_add_with_body(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        result = runner.invoke(app, [
            *(_target_flag(project_path)),
            "add",
            "--title", "With body",
            "--category", "bug",
            "--priority", "P1",
            "--body", "Some description",
        ])
        assert result.exit_code == 0
        # Verify body was stored
        import re
        match = re.search(r"Created (\S+)", result.stdout)
        assert match is not None
        item_id = match.group(1)
        show_result = runner.invoke(app, [*_target_flag(project_path), "show", item_id])
        assert "Some description" in show_result.stdout

    def test_add_with_deps(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        items_dir = project_path / "docs" / "backlog" / "items"
        _write_item(items_dir, "A")
        _write_item(items_dir, "B")
        result = runner.invoke(app, [
            *(_target_flag(project_path)),
            "add",
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
        result = runner.invoke(app, [*_target_flag(project_path), "update", "TST-001", "--title", "Updated"])
        assert result.exit_code == 0
        assert "Updated" in result.stdout

    def test_update_status(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        items_dir = project_path / "docs" / "backlog" / "items"
        _write_item(items_dir, "TST-001")
        result = runner.invoke(app, [*_target_flag(project_path), "update", "TST-001", "--status", "in_progress"])
        assert result.exit_code == 0

    def test_update_fixed(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        items_dir = project_path / "docs" / "backlog" / "items"
        _write_item(items_dir, "TST-001")
        result = runner.invoke(app, [*_target_flag(project_path), "update", "TST-001", "--fixed"])
        assert result.exit_code == 0
        show_result = runner.invoke(app, [*_target_flag(project_path), "show", "TST-001"])
        assert "done" in show_result.stdout
        assert "Fixed at" in show_result.stdout

    def test_update_missing(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        result = runner.invoke(app, [*_target_flag(project_path), "update", "TST-999", "--title", "X"])
        assert result.exit_code == 1

    def test_no_updates(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        items_dir = project_path / "docs" / "backlog" / "items"
        _write_item(items_dir, "TST-001")
        result = runner.invoke(app, [*_target_flag(project_path), "update", "TST-001"])
        assert result.exit_code == 1


class TestStatsCommand:
    def test_stats_empty(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        result = runner.invoke(app, [*_target_flag(project_path), "stats"])
        assert result.exit_code == 0
        assert "No items" in result.stdout

    def test_stats_with_items(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        items_dir = project_path / "docs" / "backlog" / "items"
        _write_item(items_dir, "TST-001", status="todo", priority="P1")
        _write_item(items_dir, "TST-002", status="done", priority="P2")
        result = runner.invoke(app, [*_target_flag(project_path), "stats"])
        assert result.exit_code == 0
        assert "Total:" in result.stdout
        assert "Active:" in result.stdout


class TestNextCommand:
    def test_next_empty(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        result = runner.invoke(app, [*_target_flag(project_path), "next"])
        assert result.exit_code == 0

    def test_next_with_items(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        items_dir = project_path / "docs" / "backlog" / "items"
        _write_item(items_dir, "TST-001", status="todo", priority="P1", impact="high", effort="XS")
        _write_item(items_dir, "TST-002", status="todo", priority="P3", impact="low", effort="XL")
        result = runner.invoke(app, [*_target_flag(project_path), "next"])
        assert result.exit_code == 0
        assert "TST-001" in result.stdout

    def test_next_json_includes_score(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        items_dir = project_path / "docs" / "backlog" / "items"
        _write_item(items_dir, "TST-001", status="todo", priority="P1", impact="high", effort="XS")
        result = runner.invoke(app, [*_target_flag(project_path), "next", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["ok"] is True
        assert data["data"][0]["id"] == "TST-001"
        assert data["data"][0]["score"] == 1500.0

    def test_next_skips_blocked(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        items_dir = project_path / "docs" / "backlog" / "items"
        _write_item(items_dir, "TST-001", status="todo")
        _write_item(items_dir, "TST-002", status="todo", depends_on=["TST-001"])
        result = runner.invoke(app, [*_target_flag(project_path), "next"])
        assert result.exit_code == 0
        # TST-002 should be blocked and not shown
        assert "TST-002" not in result.stdout


class TestIndexCommand:
    def test_index_generates(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        items_dir = project_path / "docs" / "backlog" / "items"
        _write_item(items_dir, "TST-001")
        result = runner.invoke(app, [*_target_flag(project_path), "index"])
        assert result.exit_code == 0
        assert "Generated" in result.stdout
        index_path = project_path / "docs" / "backlog" / "INDEX.md"
        assert index_path.exists()
        content = index_path.read_text()
        assert "Backlog Index" in content


class TestEditCommand:
    def test_edit_missing(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        result = runner.invoke(app, [*_target_flag(project_path), "edit", "TST-999"])
        assert result.exit_code == 1

    def test_edit_requires_tty_without_stdin(self, runner, tmp_path, monkeypatch):
        project_path = _make_backlog(tmp_path)
        items_dir = project_path / "docs" / "backlog" / "items"
        _write_item(items_dir, "TST-001")
        monkeypatch.setenv("EDITOR", "cat")
        result = runner.invoke(app, [*_target_flag(project_path), "edit", "TST-001"])
        assert result.exit_code == 1
        assert "stdout is not a TTY" in result.stdout


class TestListValidationAndFormats:
    def test_invalid_priority_raises(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        result = runner.invoke(app, [*_target_flag(project_path), "list", "--priority", "P9"])
        assert result.exit_code == 2  # Typer bad parameter exit code is usually 2
        assert "Invalid priority" in result.output

    def test_invalid_sort_raises(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        result = runner.invoke(app, [*_target_flag(project_path), "list", "--sort", "nonsense"])
        assert result.exit_code == 2
        assert "Sort option must be one of" in result.output

    def test_format_csv(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        items_dir = project_path / "docs" / "backlog" / "items"
        _write_item(items_dir, "TST-001", project="test", title="Hello CSV", priority="P0", status="todo")
        result = runner.invoke(app, [*_target_flag(project_path), "list", "--format", "csv"])
        assert result.exit_code == 0
        assert "id,status,priority,title,category,effort,impact,score" in result.output
        assert "TST-001,todo,P0,Hello CSV" in result.output


class TestUpdateValidation:
    def test_mutual_exclusion_fixed_status(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        items_dir = project_path / "docs" / "backlog" / "items"
        _write_item(items_dir, "TST-001")
        result = runner.invoke(app, [
            *_target_flag(project_path), "update", "TST-001", "--status", "cancelled", "--fixed"
        ])
        assert result.exit_code == 2
        assert "Cannot set both --fixed and a non-done --status" in result.output


class TestNextCmdRefactoring:
    def test_invalid_status_raises(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        result = runner.invoke(app, [*_target_flag(project_path), "next", "--status", "blocked"])
        assert result.exit_code == 2
        assert "Only 'todo' or 'in_progress' status can be recommended" in result.output

    def test_status_filters_todo_correctly(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        items_dir = project_path / "docs" / "backlog" / "items"
        # 写入一个 todo，和一个 in_progress
        _write_item(items_dir, "TST-001", status="todo", priority="P1")
        _write_item(items_dir, "TST-002", status="in_progress", priority="P1")
        
        # next 默认应该推荐 todo，不含 in_progress
        result = runner.invoke(app, [*_target_flag(project_path), "next"])
        assert result.exit_code == 0
        assert "TST-001" in result.stdout
        assert "TST-002" not in result.stdout

        # next --status in_progress 应该推荐 in_progress，不含 todo
        result_ip = runner.invoke(app, [*_target_flag(project_path), "next", "--status", "in_progress"])
        assert_ip_success = result_ip.exit_code == 0
        assert assert_ip_success
        assert "TST-002" in result_ip.stdout
        assert "TST-001" not in result_ip.stdout


class TestCLIValidationAndSecurity:
    def test_negative_limit_raises(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        
        # list limit
        result_list = runner.invoke(app, [*_target_flag(project_path), "list", "--limit", "-1"])
        assert result_list.exit_code == 2
        assert "Limit must be a non-negative integer" in result_list.output
        
        # next limit
        result_next = runner.invoke(app, [*_target_flag(project_path), "next", "--limit", "-1"])
        assert result_next.exit_code == 2
        assert "Limit must be a non-negative integer" in result_next.output

    def test_expected_revision_mismatch_raises(self, runner, tmp_path):
        project_path = _make_backlog(tmp_path)
        items_dir = project_path / "docs" / "backlog" / "items"
        _write_item(items_dir, "TST-001")
        
        # Try updating with mismatching revision
        result = runner.invoke(app, [
            *_target_flag(project_path), "update", "TST-001", "--title", "New Title", "--expected-revision", "wrong-rev"
        ])
        assert result.exit_code == 1
        assert "Revision mismatch" in result.output

    def test_click_exception_renders_json(self, runner, tmp_path):
        import sys

        import pytest
        orig_argv = list(sys.argv)
        sys.argv = ["backlog", "list", "--priority", "P9", "--json"]
        try:
            from backlog.cli import run_cli
            with pytest.raises(SystemExit) as excinfo:
                run_cli()
            assert excinfo.value.code == 2
        finally:
            sys.argv = orig_argv

    def test_general_exception_renders_json(self, runner, tmp_path):
        import sys

        import pytest
        project_path = _make_backlog(tmp_path)
        items_dir = project_path / "docs" / "backlog" / "items"
        _write_item(items_dir, "TST-001")
        
        orig_argv = list(sys.argv)
        sys.argv = [
            "backlog",
            "--target", str(project_path),
            "update", "TST-001",
            "--title", "New Title",
            "--expected-revision", "wrong-rev",
            "--json"
        ]
        try:
            from backlog.cli import run_cli
            with pytest.raises(SystemExit) as excinfo:
                run_cli()
            assert excinfo.value.code == 1
        finally:
            sys.argv = orig_argv

