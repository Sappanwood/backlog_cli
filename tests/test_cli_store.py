"""CLI contract tests for the exact portable store entrypoint."""

import json
import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

from backlog.cli import app


def _make_store(tmp_path: Path) -> Path:
    root = tmp_path / "backlog"
    root.mkdir()
    (root / "backlog.json").write_text(
        json.dumps(
            {
                "schema": "backlog/Store@1",
                "project_id": "portable-project",
                "id_prefix": "POR",
            }
        ),
        encoding="utf-8",
    )
    (root / "items").mkdir()
    (root / "INDEX.md").write_text("# Backlog Index\n", encoding="utf-8")
    return root


def _write_item(root: Path, item_id: str) -> None:
    (root / "items" / f"{item_id}.md").write_text(
        "---\n"
        f"id: {item_id}\n"
        "project: portable-project\n"
        "title: Portable item\n"
        "category: feature\n"
        "priority: P2\n"
        "effort: M\n"
        "impact: medium\n"
        "status: todo\n"
        "source: ''\n"
        "tags: []\n"
        "depends_on: []\n"
        "related_docs: []\n"
        "created: '2026-08-01'\n"
        "updated: '2026-08-01'\n"
        "---\n",
        encoding="utf-8",
    )


class TestExactStoreCommand:
    def test_target_option_is_rejected(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(app, ["--target", str(tmp_path), "list", "--json"])

        assert result.exit_code == 2
        assert "No such option: --target" in result.output

    def test_store_is_required(self) -> None:
        result = CliRunner().invoke(app, ["list", "--json"])

        assert result.exit_code == 2
        assert "Missing option '--store'" in result.output

    def test_exact_read_does_not_create_lock_or_other_entries(self, tmp_path: Path) -> None:
        root = _make_store(tmp_path)
        before = sorted(path.name for path in root.iterdir())

        result = CliRunner().invoke(app, ["--store", str(root), "list", "--json"])

        assert result.exit_code == 0
        assert sorted(path.name for path in root.iterdir()) == before

    def test_store_does_not_discover_child_backlog(self, tmp_path: Path) -> None:
        root = _make_store(tmp_path)

        result = subprocess.run(
            [sys.executable, "-m", "backlog.cli", "--store", str(root.parent), "list", "--json"],
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode != 0
        assert json.loads(result.stdout)["error"]["code"] == "STORE_INVALID"

    def test_removed_provision_command_is_rejected(self, tmp_path: Path) -> None:
        root = _make_store(tmp_path)

        result = CliRunner().invoke(app, ["--store", str(root), "provision-store"])

        assert result.exit_code == 2
        assert "No such command 'provision-store'" in result.output

    def test_validate_store_uses_manifest_identity(self, tmp_path: Path) -> None:
        root = _make_store(tmp_path)
        _write_item(root, "POR-001")

        result = CliRunner().invoke(app, ["--store", str(root), "validate-store", "--json"])

        assert result.exit_code == 0
        assert json.loads(result.stdout)["data"] == {
            "store": str(root),
            "project_id": "portable-project",
            "id_prefix": "POR",
            "items": 1,
        }

    def test_validate_store_rejects_removed_identity_options(self, tmp_path: Path) -> None:
        root = _make_store(tmp_path)

        result = CliRunner().invoke(
            app,
            ["--store", str(root), "validate-store", "--project-id", "portable-project"],
        )

        assert result.exit_code == 2
        assert "No such option: --project-id" in result.output
