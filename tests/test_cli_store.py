"""CLI contract tests for exact portable store entrypoints."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

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


def _write_item(root: Path, item_id: str, *, project: str = "portable-project") -> None:
    (root / "items" / f"{item_id}.md").write_text(
        "---\n"
        f"id: {item_id}\n"
        f"project: {project}\n"
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
    def test_exact_store_and_legacy_target_return_equivalent_results(self, tmp_path):
        root = _make_store(tmp_path)
        _write_item(root, "POR-001")
        runner = CliRunner()

        exact = runner.invoke(app, ["--store", str(root), "list", "--json"])
        legacy = runner.invoke(app, ["--target", str(tmp_path), "list", "--json"])

        assert exact.exit_code == legacy.exit_code == 0
        assert json.loads(exact.stdout) == json.loads(legacy.stdout)

    def test_exact_read_does_not_create_lock_or_other_entries(self, tmp_path):
        root = _make_store(tmp_path)
        before = sorted(path.name for path in root.iterdir())

        result = CliRunner().invoke(app, ["--store", str(root), "list", "--json"])

        assert result.exit_code == 0
        assert sorted(path.name for path in root.iterdir()) == before

    def test_store_does_not_discover_child_backlog(self, tmp_path):
        root = _make_store(tmp_path)

        result = subprocess.run(
            [sys.executable, "-m", "backlog.cli", "--store", str(root.parent), "list", "--json"],
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode != 0
        payload = json.loads(result.stdout)
        assert payload["error"]["code"] == "STORE_INVALID"

    def test_store_and_target_are_rejected_as_ambiguous_json_input(self, tmp_path):
        root = _make_store(tmp_path)

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "backlog.cli",
                "--store",
                str(root),
                "--target",
                str(root.parent),
                "list",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode != 0
        payload = json.loads(result.stdout)
        assert payload["error"]["code"] == "INVALID_INPUT"

    def test_format_equals_json_renders_store_errors_before_callback(self, tmp_path):
        root = _make_store(tmp_path)

        invalid_store = subprocess.run(
            [sys.executable, "-m", "backlog.cli", "--store", str(root.parent), "list", "--format=json"],
            capture_output=True,
            text=True,
            check=False,
        )
        ambiguous = subprocess.run(
            [
                sys.executable,
                "-m",
                "backlog.cli",
                "--store",
                str(root),
                "--target",
                str(root.parent),
                "list",
                "--format=json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        assert invalid_store.returncode != 0
        assert json.loads(invalid_store.stdout)["error"]["code"] == "STORE_INVALID"
        assert ambiguous.returncode != 0
        assert json.loads(ambiguous.stdout)["error"]["code"] == "INVALID_INPUT"

    def test_cwd_discovery_uses_discovered_store_without_read_side_effects(self, tmp_path, monkeypatch):
        project = tmp_path / "portable-project"
        root = project / "docs" / "backlog"
        root.mkdir(parents=True)
        (root / "backlog.json").write_text(
            json.dumps({"schema": "backlog/Store@1", "project_id": "portable-project", "id_prefix": "POR"}),
            encoding="utf-8",
        )
        (root / "items").mkdir()
        (root / "INDEX.md").write_text("# Backlog Index\n", encoding="utf-8")
        _write_item(root, "POR-001")
        nested = project / "src" / "deep"
        nested.mkdir(parents=True)
        before = sorted(path.name for path in root.iterdir())
        monkeypatch.chdir(nested)
        runner = CliRunner()

        discovered = runner.invoke(app, ["list", "--json"])
        exact = runner.invoke(app, ["--store", str(root), "list", "--json"])

        assert discovered.exit_code == exact.exit_code == 0
        assert json.loads(discovered.stdout) == json.loads(exact.stdout)
        assert sorted(path.name for path in root.iterdir()) == before

    def test_cwd_legacy_mutation_uses_discovered_host_identity(self, tmp_path, monkeypatch):
        project = tmp_path / "project"
        (project / "docs" / "backlog" / "items").mkdir(parents=True)
        nested = project / "src" / "deep"
        nested.mkdir(parents=True)
        monkeypatch.chdir(nested)

        result = CliRunner().invoke(
            app,
            ["add", "--title", "CWD item", "--category", "feature", "--priority", "P2", "--json"],
        )

        assert result.exit_code == 0
        item_id = json.loads(result.stdout)["data"]["id"]
        item_text = (project / "docs" / "backlog" / "items" / f"{item_id}.md").read_text(encoding="utf-8")
        assert item_id == "PRO-001"
        assert "project: project" in item_text


class TestProvisionStoreCommand:
    @staticmethod
    def _wait_for_ready(paths: list[Path]) -> None:
        deadline = time.monotonic() + 5
        while not all(path.exists() for path in paths):
            assert time.monotonic() < deadline, "child processes did not reach the concurrency barrier"
            time.sleep(0.01)

    def test_provisions_validated_legacy_store_with_explicit_identity(self, tmp_path):
        root = tmp_path / "docs" / "backlog"
        (root / "items").mkdir(parents=True)
        (root / "INDEX.md").write_text("# Backlog Index\n", encoding="utf-8")
        _write_item(root, "LEG-001", project="legacy-project")

        result = CliRunner().invoke(
            app,
            [
                "--target",
                str(tmp_path),
                "provision-store",
                "--project-id",
                "legacy-project",
                "--id-prefix",
                "LEG",
            ],
        )

        assert result.exit_code == 0
        assert json.loads((root / "backlog.json").read_text(encoding="utf-8")) == {
            "schema": "backlog/Store@1",
            "project_id": "legacy-project",
            "id_prefix": "LEG",
        }
        assert not (root / ".lock").exists()

    def test_provision_rejects_conflicting_identity_without_manifest(self, tmp_path):
        root = tmp_path / "docs" / "backlog"
        (root / "items").mkdir(parents=True)
        (root / "INDEX.md").write_text("# Backlog Index\n", encoding="utf-8")
        _write_item(root, "LEG-001", project="legacy-project")

        result = CliRunner().invoke(
            app,
            [
                "--target",
                str(tmp_path),
                "provision-store",
                "--project-id",
                "other-project",
                "--id-prefix",
                "LEG",
            ],
        )

        assert result.exit_code == 1
        assert not (root / "backlog.json").exists()

    def test_provision_never_overwrites_existing_manifest(self, tmp_path):
        root = _make_store(tmp_path)
        before = (root / "backlog.json").read_bytes()

        result = CliRunner().invoke(
            app,
            [
                "--target",
                str(tmp_path),
                "provision-store",
                "--project-id",
                "portable-project",
                "--id-prefix",
                "POR",
            ],
        )

        assert result.exit_code == 1
        assert (root / "backlog.json").read_bytes() == before

    def test_provision_preserves_preexisting_temporary_manifest_entry(self, tmp_path, monkeypatch):
        root = tmp_path / "docs" / "backlog"
        (root / "items").mkdir(parents=True)
        (root / "INDEX.md").write_text("# Backlog Index\n", encoding="utf-8")
        temporary = root / ".backlog.json.collision.tmp"
        temporary.write_text("keep", encoding="utf-8")
        monkeypatch.setattr("backlog.items.uuid.uuid4", lambda: SimpleNamespace(hex="collision"))

        result = CliRunner().invoke(
            app,
            [
                "--target",
                str(tmp_path),
                "provision-store",
                "--project-id",
                "legacy-project",
                "--id-prefix",
                "LEG",
            ],
        )

        assert result.exit_code == 1
        assert temporary.read_text(encoding="utf-8") == "keep"
        assert not (root / "backlog.json").exists()

    def test_provision_ignores_legacy_regular_lock_residue(self, tmp_path):
        root = tmp_path / "docs" / "backlog"
        (root / "items").mkdir(parents=True)
        (root / "INDEX.md").write_text("# Backlog Index\n", encoding="utf-8")
        (root / ".lock").write_text("legacy residue", encoding="utf-8")

        result = CliRunner().invoke(
            app,
            [
                "--target",
                str(tmp_path),
                "provision-store",
                "--project-id",
                "legacy-project",
                "--id-prefix",
                "LEG",
            ],
        )

        assert result.exit_code == 0
        assert (root / ".lock").read_text(encoding="utf-8") == "legacy residue"

    def test_validate_store_rejects_malformed_and_model_invalid_legacy_items_without_writing(self, tmp_path):
        root = tmp_path / "docs" / "backlog"
        (root / "items").mkdir(parents=True)
        (root / "INDEX.md").write_text("# Backlog Index\n", encoding="utf-8")
        invalid = root / "items" / "LEG-001.md"
        invalid.write_text("---\nid: LEG-001\nproject: legacy\nstatus: impossible\n---\n", encoding="utf-8")
        before = invalid.read_bytes()

        result = CliRunner().invoke(
            app,
            ["--target", str(tmp_path), "validate-store", "--project-id", "legacy", "--id-prefix", "LEG", "--json"],
        )

        assert result.exit_code == 1
        assert json.loads(result.stdout)["error"]["code"] == "STORE_INVALID"
        assert invalid.read_bytes() == before
        assert not (root / "backlog.json").exists()

    def test_cwd_provisioning_uses_discovered_legacy_store(self, tmp_path, monkeypatch):
        project = tmp_path / "project"
        root = project / "docs" / "backlog"
        (root / "items").mkdir(parents=True)
        (root / "INDEX.md").write_text("# Backlog Index\n", encoding="utf-8")
        nested = project / "src" / "deep"
        nested.mkdir(parents=True)
        monkeypatch.chdir(nested)

        result = CliRunner().invoke(
            app,
            ["provision-store", "--project-id", "project", "--id-prefix", "PRO"],
        )

        assert result.exit_code == 0
        assert (root / "backlog.json").exists()

    def test_provision_rejects_invalid_items_without_creating_manifest(self, tmp_path):
        root = tmp_path / "docs" / "backlog"
        (root / "items").mkdir(parents=True)
        (root / "INDEX.md").write_text("# Backlog Index\n", encoding="utf-8")
        (root / "items" / "LEG-001.md").write_text("not frontmatter", encoding="utf-8")

        result = CliRunner().invoke(
            app,
            ["--target", str(tmp_path), "provision-store", "--project-id", "legacy", "--id-prefix", "LEG"],
        )

        assert result.exit_code == 1
        assert not (root / "backlog.json").exists()

    def test_provision_rejects_symlink_item_and_mixed_prefix_without_manifest(self, tmp_path):
        root = tmp_path / "docs" / "backlog"
        (root / "items").mkdir(parents=True)
        (root / "INDEX.md").write_text("# Backlog Index\n", encoding="utf-8")
        outside = tmp_path / "outside.md"
        outside.write_text("outside", encoding="utf-8")
        (root / "items" / "LEG-001.md").symlink_to(outside)

        symlink_result = CliRunner().invoke(
            app,
            ["--target", str(tmp_path), "provision-store", "--project-id", "legacy", "--id-prefix", "LEG"],
        )
        (root / "items" / "LEG-001.md").unlink()
        _write_item(root, "LEG-001", project="legacy")
        _write_item(root, "OTH-001", project="legacy")
        prefix_result = CliRunner().invoke(
            app,
            ["--target", str(tmp_path), "provision-store", "--project-id", "legacy", "--id-prefix", "LEG"],
        )

        assert symlink_result.exit_code == prefix_result.exit_code == 1
        assert not (root / "backlog.json").exists()

    def test_provision_requires_existing_root_items_and_index(self, tmp_path):
        runner = CliRunner()
        missing_root = runner.invoke(
            app,
            ["--target", str(tmp_path), "provision-store", "--project-id", "legacy", "--id-prefix", "LEG"],
        )
        root = tmp_path / "docs" / "backlog"
        root.mkdir(parents=True)
        missing_items = runner.invoke(
            app,
            ["--target", str(tmp_path), "provision-store", "--project-id", "legacy", "--id-prefix", "LEG"],
        )
        (root / "items").mkdir()
        missing_index = runner.invoke(
            app,
            ["--target", str(tmp_path), "provision-store", "--project-id", "legacy", "--id-prefix", "LEG"],
        )

        assert missing_root.exit_code == missing_items.exit_code == missing_index.exit_code == 1
        assert not (root / "backlog.json").exists()

    def test_concurrent_provisioners_publish_one_complete_manifest(self, tmp_path):
        project = tmp_path / "project"
        root = project / "docs" / "backlog"
        (root / "items").mkdir(parents=True)
        (root / "INDEX.md").write_text("# Backlog Index\n", encoding="utf-8")
        gate = tmp_path / "gate"
        ready_paths = [tmp_path / "ready-1", tmp_path / "ready-2"]
        child_code = """
import os
import time
from pathlib import Path
from backlog.items import provision_legacy_store

Path(os.environ["READY"]).write_text("ready")
while not Path(os.environ["GATE"]).exists():
    time.sleep(0.01)
try:
    provision_legacy_store(
        Path(os.environ["TARGET"]),
        project_id=os.environ["PROJECT_ID"],
        id_prefix=os.environ["ID_PREFIX"],
    )
except ValueError:
    print("rejected")
else:
    print("provisioned")
"""
        processes = []
        try:
            for ready_path, project_id, id_prefix in zip(
                ready_paths,
                ("legacy-one", "legacy-two"),
                ("ONE", "TWO"),
                strict=True,
            ):
                processes.append(
                    subprocess.Popen(
                        [sys.executable, "-c", child_code],
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        env=os.environ
                        | {
                            "TARGET": str(project),
                            "GATE": str(gate),
                            "READY": str(ready_path),
                            "PROJECT_ID": project_id,
                            "ID_PREFIX": id_prefix,
                        },
                    )
                )
            self._wait_for_ready(ready_paths)
            gate.write_text("go", encoding="utf-8")
            results = [process.communicate(timeout=5) for process in processes]
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()

        assert [stderr for _, stderr in results] == ["", ""]
        assert sorted(stdout.strip() for stdout, _ in results) == ["provisioned", "rejected"]
        assert json.loads((root / "backlog.json").read_text(encoding="utf-8")) in (
            {"schema": "backlog/Store@1", "project_id": "legacy-one", "id_prefix": "ONE"},
            {"schema": "backlog/Store@1", "project_id": "legacy-two", "id_prefix": "TWO"},
        )
        assert not list(root.glob(".backlog.json.*.tmp"))

    def test_provision_and_legacy_mutation_share_lock_without_identity_corruption(self, tmp_path):
        project = tmp_path / "project"
        root = project / "docs" / "backlog"
        (root / "items").mkdir(parents=True)
        (root / "INDEX.md").write_text("# Backlog Index\n", encoding="utf-8")
        gate = tmp_path / "gate"
        ready_paths = [tmp_path / "ready-provision", tmp_path / "ready-add"]
        child_code = """
import os
import time
from pathlib import Path
from backlog.items import add_legacy_item, provision_legacy_store
from backlog.models import BacklogItem, Category, Priority

Path(os.environ["READY"]).write_text("ready")
while not Path(os.environ["GATE"]).exists():
    time.sleep(0.01)
if os.environ["ROLE"] == "provision":
    provision_legacy_store(Path(os.environ["TARGET"]), project_id="legacy", id_prefix="LEG")
else:
    add_legacy_item(
        BacklogItem(id="AUTO", project="legacy", title="Concurrent", category=Category.FEATURE, priority=Priority.P1),
        Path(os.environ["TARGET"]),
    )
print("ok")
"""
        processes = []
        try:
            for ready_path, role in zip(ready_paths, ("provision", "add"), strict=True):
                processes.append(
                    subprocess.Popen(
                        [sys.executable, "-c", child_code],
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        env=os.environ
                        | {"TARGET": str(project), "GATE": str(gate), "READY": str(ready_path), "ROLE": role},
                    )
                )
            self._wait_for_ready(ready_paths)
            gate.write_text("go", encoding="utf-8")
            results = [process.communicate(timeout=5) for process in processes]
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()

        assert [stdout.strip() for stdout, _ in results] == ["ok", "ok"]
        assert [stderr for _, stderr in results] == ["", ""]
        manifest = json.loads((root / "backlog.json").read_text(encoding="utf-8"))
        assert manifest["project_id"] == "legacy"
        assert manifest["id_prefix"] == "LEG"
        item = (root / "items" / "LEG-001.md").read_text(encoding="utf-8")
        assert "project: legacy" in item

    def test_stale_legacy_context_rejects_after_conflicting_provision(self, tmp_path):
        project = tmp_path / "project"
        root = project / "docs" / "backlog"
        (root / "items").mkdir(parents=True)
        (root / "INDEX.md").write_text("# Original Index\n", encoding="utf-8")
        index_before = (root / "INDEX.md").read_bytes()
        gate = tmp_path / "gate"
        ready = tmp_path / "ready"
        child_code = """
import os
import time
from pathlib import Path
from backlog.items import _legacy_store_context, add_item
from backlog.models import BacklogItem, Category, Priority

store = _legacy_store_context(Path(os.environ["TARGET"]), "mutation", create=True, id_prefix="MUT")
Path(os.environ["READY"]).write_text("parsed")
while not Path(os.environ["GATE"]).exists():
    time.sleep(0.01)
try:
    add_item(
        BacklogItem(
            id="AUTO",
            project="mutation",
            title="Must reject",
            category=Category.FEATURE,
            priority=Priority.P1,
        ),
        store,
    )
except ValueError:
    print("rejected")
else:
    print("written")
"""
        process = subprocess.Popen(
            [sys.executable, "-c", child_code],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=os.environ | {"TARGET": str(project), "GATE": str(gate), "READY": str(ready)},
        )
        try:
            self._wait_for_ready([ready])
            from backlog.items import provision_legacy_store

            provision_legacy_store(project, project_id="provision", id_prefix="PRO")
            gate.write_text("go", encoding="utf-8")
            stdout, stderr = process.communicate(timeout=5)
        finally:
            if process.poll() is None:
                process.kill()

        assert stdout.strip() == "rejected"
        assert stderr == ""
        assert json.loads((root / "backlog.json").read_text(encoding="utf-8")) == {
            "schema": "backlog/Store@1",
            "project_id": "provision",
            "id_prefix": "PRO",
        }
        assert list((root / "items").glob("*.md")) == []
        assert (root / "INDEX.md").read_bytes() == index_before
