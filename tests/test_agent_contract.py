"""Contract tests for the five official Agent JSON operations."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from backlog.cli import app


def _make_store(
    tmp_path: Path,
    *,
    project_id: str = "agent-project",
    id_prefix: str = "AGT",
) -> Path:
    store = tmp_path / "backlog"
    store.mkdir()
    (store / "backlog.json").write_text(
        json.dumps(
            {
                "schema": "backlog/Store@1",
                "project_id": project_id,
                "id_prefix": id_prefix,
            }
        ),
        encoding="utf-8",
    )
    (store / "items").mkdir()
    (store / "INDEX.md").write_text("# Backlog Index\n", encoding="utf-8")
    return store


def _write_item(
    store: Path,
    item_id: str,
    *,
    status: str = "todo",
    depends_on: list[str] | None = None,
    project_id: str = "agent-project",
    item_type: str | None = None,
    parent_id: str | None = None,
) -> None:
    dependency_lines = ", ".join(depends_on or [])
    hierarchy = ""
    if item_type is not None:
        hierarchy += f"item_type: {item_type}\n"
    if parent_id is not None:
        hierarchy += f"parent_id: {parent_id}\n"
    (store / "items" / f"{item_id}.md").write_text(
        "---\n"
        f"id: {item_id}\n"
        f"project: {project_id}\n"
        f"title: {item_id} title\n"
        f"{hierarchy}"
        "category: feature\n"
        "priority: P2\n"
        "effort: M\n"
        "impact: medium\n"
        f"status: {status}\n"
        "source: ''\n"
        "tags: []\n"
        f"depends_on: [{dependency_lines}]\n"
        "related_docs: []\n"
        "created: '2026-08-01'\n"
        "updated: '2026-08-01'\n"
        "revision: original\n"
        "---\n",
        encoding="utf-8",
    )


def _json(runner: CliRunner, args: list[str]) -> dict:
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def test_agent_list_and_show_include_effective_status_and_blockers(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    _write_item(store, "AGT-001")
    _write_item(store, "AGT-002", depends_on=["AGT-001"])
    runner = CliRunner()

    listed = _json(runner, ["--store", str(store), "list", "--json"])
    blocked = next(item for item in listed["data"] if item["id"] == "AGT-002")
    assert blocked["effective_status"] == "blocked"
    assert blocked["blocked_by"] == ["AGT-001"]

    shown = _json(runner, ["--store", str(store), "show", "AGT-002", "--json"])
    assert shown["data"]["blocked_by"] == ["AGT-001"]


def test_legacy_items_default_to_task_without_a_parent(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    _write_item(store, "AGT-001")

    shown = _json(CliRunner(), ["--store", str(store), "show", "AGT-001", "--json"])

    assert shown["data"]["item_type"] == "task"
    assert shown["data"]["parent_id"] is None


def test_agent_adds_epic_and_child_but_only_queues_the_child(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    runner = CliRunner()
    epic = _json(
        runner,
        [
            "--store", str(store), "add",
            "--title", "Data foundation", "--category", "feature", "--priority", "P1",
            "--item-type", "epic", "--json",
        ],
    )["data"]["result"]
    child = _json(
        runner,
        [
            "--store", str(store), "add",
            "--title", "Define the data contract", "--category", "architecture", "--priority", "P1",
            "--parent-id", epic["id"], "--json",
        ],
    )["data"]["result"]

    assert epic["item_type"] == "epic"
    assert epic["parent_id"] is None
    assert child["item_type"] == "task"
    assert child["parent_id"] == epic["id"]
    queue = _json(runner, ["--store", str(store), "next", "--json"])["data"]
    assert [item["id"] for item in queue] == [child["id"]]
    epics = _json(runner, ["--store", str(store), "list", "--item-type", "epic", "--json"])["data"]
    assert [item["id"] for item in epics] == [epic["id"]]


def test_agent_can_clear_a_parent_but_cannot_demote_an_epic_with_children(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    _write_item(store, "AGT-001", item_type="epic")
    _write_item(store, "AGT-002", item_type="task", parent_id="AGT-001")
    runner = CliRunner()

    invalid = runner.invoke(
        app,
        ["--store", str(store), "update", "AGT-001", "--item-type", "task", "--json"],
    )
    assert invalid.exit_code == 1
    assert "must be an epic" in json.loads(invalid.stdout)["error"]["message"]

    cleared = _json(
        runner,
        ["--store", str(store), "update", "AGT-002", "--clear-parent", "--json"],
    )["data"]
    assert cleared["changed_fields"] == ["parent_id"]
    assert cleared["result"]["parent_id"] is None


@pytest.mark.parametrize(
    ("parent_id", "expected_message"),
    [
        ("AGT-404", "Parent item 'AGT-404' does not exist"),
        ("AGT-001", "Parent item 'AGT-001' must be an epic"),
    ],
)
def test_agent_rejects_invalid_parent_relation(
    tmp_path: Path,
    parent_id: str,
    expected_message: str,
) -> None:
    store = _make_store(tmp_path)
    _write_item(store, "AGT-001")

    result = CliRunner().invoke(
        app,
        [
            "--store", str(store), "add",
            "--title", "Invalid child", "--category", "feature", "--priority", "P2",
            "--parent-id", parent_id, "--json",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "INVALID_INPUT"
    assert expected_message in payload["error"]["message"]


def test_agent_list_uses_full_store_for_blockers_after_filters_and_limit(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    _write_item(store, "AGT-001", depends_on=["AGT-002"])
    _write_item(store, "AGT-002", status="done")
    runner = CliRunner()

    filtered = _json(runner, ["--store", str(store), "list", "--status", "todo", "--json"])
    assert [item["id"] for item in filtered["data"]] == ["AGT-001"]
    assert filtered["data"][0]["effective_status"] == "todo"
    assert filtered["data"][0]["blocked_by"] == []

    limited = _json(runner, ["--store", str(store), "list", "--sort", "id", "--limit", "1", "--json"])
    assert limited["data"][0]["id"] == "AGT-001"
    assert limited["data"][0]["blocked_by"] == []


def test_agent_add_returns_full_mutation_receipt_and_maintains_index(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    payload = _json(
        CliRunner(),
        [
            "--store",
            str(store),
            "add",
            "--title",
            "Created by agent",
            "--category",
            "feature",
            "--priority",
            "P1",
            "--json",
        ],
    )

    receipt = payload["data"]
    assert receipt["before"] is None
    assert receipt["no_op"] is False
    assert receipt["result"]["id"] == "AGT-001"
    assert receipt["revision"] == receipt["result"]["revision"]
    assert "id" in receipt["changed_fields"]
    assert "AGT-001" in (store / "INDEX.md").read_text(encoding="utf-8")


def test_agent_update_patch_noop_fixed_date_and_revision_conflict(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    _write_item(store, "AGT-001")
    runner = CliRunner()

    noop = _json(
        runner,
        ["--store", str(store), "update", "AGT-001", "--title", "AGT-001 title", "--json"],
    )["data"]
    assert noop["no_op"] is True
    assert noop["changed_fields"] == []
    assert noop["before"] == noop["result"]
    assert noop["revision"] == "original"

    changed = _json(
        runner,
        [
            "--store",
            str(store),
            "update",
            "AGT-001",
            "--status",
            "done",
            "--expected-revision",
            "original",
            "--json",
        ],
    )["data"]
    assert changed["no_op"] is False
    assert changed["result"]["fixed_at"] is not None
    assert changed["revision"] != "original"

    conflict = runner.invoke(
        app,
        [
            "--store",
            str(store),
            "update",
            "AGT-001",
            "--title",
            "will conflict",
            "--expected-revision",
            "original",
            "--json",
        ],
    )
    assert conflict.exit_code == 1
    assert json.loads(conflict.stdout)["error"]["code"] == "REVISION_MISMATCH"


def test_agent_next_returns_in_progress_ready_and_blocked_queue_states(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    _write_item(store, "AGT-001", status="in_progress")
    _write_item(store, "AGT-002")
    _write_item(store, "AGT-003", depends_on=["AGT-002"])

    payload = _json(CliRunner(), ["--store", str(store), "next", "--json"])
    queue = {item["id"]: item for item in payload["data"]}
    assert queue["AGT-001"]["queue_state"] == "in_progress"
    assert queue["AGT-002"]["queue_state"] == "ready"
    assert queue["AGT-003"]["queue_state"] == "blocked"
    assert queue["AGT-003"]["blocked_by"] == ["AGT-002"]
    assert queue["AGT-002"]["score_hint"]["value"] == 40.0
    assert "score" not in queue["AGT-002"]


def test_agent_next_honors_explicit_effective_status_filter(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    _write_item(store, "AGT-001", status="in_progress")
    _write_item(store, "AGT-002")
    _write_item(store, "AGT-003", depends_on=["AGT-002"])
    runner = CliRunner()

    for status, expected_id in (("in_progress", "AGT-001"), ("todo", "AGT-002"), ("blocked", "AGT-003")):
        payload = _json(runner, ["--store", str(store), "next", "--status", status, "--json"])
        assert [item["id"] for item in payload["data"]] == [expected_id]


def test_agent_dependency_status_matrix_and_queue(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    _write_item(store, "AGT-001", status="done")
    _write_item(store, "AGT-002", status="cancelled")
    _write_item(store, "AGT-003")
    _write_item(store, "AGT-004", depends_on=["AGT-MISSING"])
    _write_item(store, "AGT-005", depends_on=["AGT-002"])
    _write_item(store, "AGT-006", depends_on=["AGT-001"])
    _write_item(store, "AGT-007", status="blocked")
    _write_item(store, "AGT-008", depends_on=["AGT-007"])

    listed = _json(CliRunner(), ["--store", str(store), "list", "--json"])
    records = {item["id"]: item for item in listed["data"]}
    assert records["AGT-004"]["blocked_by"] == ["AGT-MISSING"]
    assert records["AGT-005"]["blocked_by"] == ["AGT-002"]
    assert records["AGT-006"]["blocked_by"] == []
    assert records["AGT-007"]["blocked_by"] == []
    assert records["AGT-008"]["blocked_by"] == ["AGT-007"]

    queue = _json(CliRunner(), ["--store", str(store), "next", "--limit", "10", "--json"])["data"]
    states = {item["id"]: item["queue_state"] for item in queue}
    assert states == {
        "AGT-003": "ready",
        "AGT-004": "blocked",
        "AGT-005": "blocked",
        "AGT-006": "ready",
        "AGT-007": "blocked",
        "AGT-008": "blocked",
    }


def test_agent_json_failures_and_empty_queue_use_stable_contract(tmp_path: Path) -> None:
    store = _make_store(tmp_path)

    empty = _json(CliRunner(), ["--store", str(store), "next", "--json"])
    assert empty == {"ok": True, "data": []}

    invalid = subprocess.run(
        [sys.executable, "-m", "backlog.cli", "--store", str(store), "update", "AGT-001", "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert invalid.returncode == 1
    assert json.loads(invalid.stdout)["error"]["code"] == "INVALID_INPUT"

    missing = subprocess.run(
        [sys.executable, "-m", "backlog.cli", "--store", str(store), "show", "AGT-001", "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert missing.returncode == 1
    assert json.loads(missing.stdout)["error"]["code"] == "ITEM_NOT_FOUND"

@pytest.mark.parametrize("status", ["done", "cancelled"])
def test_agent_invalid_next_status_has_stable_json_error(tmp_path: Path, status: str) -> None:
    store = _make_store(tmp_path)
    invalid_queue_filter = subprocess.run(
        [
            sys.executable,
            "-m",
            "backlog.cli",
            "--store",
            str(store),
            "next",
            "--status",
            status,
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert invalid_queue_filter.returncode == 2
    assert json.loads(invalid_queue_filter.stdout)["error"]["code"] == "INVALID_INPUT"


def test_agent_noop_preserves_item_and_index_bytes_and_mtime(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    _write_item(store, "AGT-001")
    item_path = store / "items" / "AGT-001.md"
    index_path = store / "INDEX.md"
    before_item = item_path.read_bytes()
    before_index = index_path.read_bytes()
    before_item_mtime = item_path.stat().st_mtime_ns
    before_index_mtime = index_path.stat().st_mtime_ns

    receipt = _json(
        CliRunner(),
        ["--store", str(store), "update", "AGT-001", "--title", "AGT-001 title", "--json"],
    )["data"]
    assert receipt["no_op"] is True
    assert item_path.read_bytes() == before_item
    assert index_path.read_bytes() == before_index
    assert item_path.stat().st_mtime_ns == before_item_mtime
    assert index_path.stat().st_mtime_ns == before_index_mtime


def test_agent_update_reports_actual_changes_and_before_after(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    _write_item(store, "AGT-001")

    receipt = _json(
        CliRunner(),
        ["--store", str(store), "update", "AGT-001", "--title", "Updated title", "--json"],
    )["data"]
    assert receipt["changed_fields"] == ["title"]
    assert receipt["before"]["title"] == "AGT-001 title"
    assert receipt["result"]["title"] == "Updated title"
    assert receipt["before"]["revision"] == "original"
    assert receipt["result"]["revision"] == receipt["revision"]
