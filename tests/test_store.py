"""Tests for the portable Backlog Store contract."""

import concurrent.futures
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from backlog.items import (
    BacklogItemParseError,
    add_item,
    generate_index,
    list_items,
    show_item,
    update_item,
)
from backlog.models import BacklogItem, Category, Priority, Status
from backlog.store import StoreLoadError, StoreManifest, load_store


def _make_store(tmp_path: Path) -> Path:
    root = tmp_path / "portable-store"
    root.mkdir()
    (root / "backlog.json").write_text(
        json.dumps(
            {
                "schema": "backlog/Store@1",
                "project_id": "portable-project",
                "id_prefix": "POR",
            }
        )
    )
    (root / "items").mkdir()
    (root / "INDEX.md").write_text("# Backlog Index\n")
    return root


def _store_snapshot(root: Path) -> list[tuple[str, int, bytes | str | None]]:
    """Recursively capture store entry types and regular-file bytes without following links."""
    entries: list[tuple[str, int, bytes | str | None]] = []

    def capture(directory: Path) -> None:
        for entry in sorted(directory.iterdir()):
            mode = entry.lstat().st_mode
            relative = str(entry.relative_to(root))
            if stat.S_ISREG(mode):
                content: bytes | str | None = entry.read_bytes()
            elif stat.S_ISLNK(mode):
                content = os.readlink(entry)
            else:
                content = None
            entries.append((relative, mode, content))
            if stat.S_ISDIR(mode):
                capture(entry)

    capture(root)
    return entries


def _write_item(root: Path, item_id: str, **overrides: object) -> None:
    defaults: dict[str, object] = {
        "id": item_id,
        "project": "portable-project",
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
        "created": "2026-08-01",
        "updated": "2026-08-01",
    }
    defaults.update(overrides)
    lines = ["---"]
    for key, value in defaults.items():
        lines.append(f"{key}: {json.dumps(value)}")
    lines.extend(["---", ""])
    (root / "items" / f"{item_id}.md").write_text("\n".join(lines), encoding="utf-8")


class TestStoreManifest:
    def test_accepts_store_v1_identity(self):
        manifest = StoreManifest.model_validate(
            {
                "schema": "backlog/Store@1",
                "project_id": "portable-project_2",
                "id_prefix": "POR_2",
            }
        )

        assert manifest.store_schema == "backlog/Store@1"
        assert manifest.project_id == "portable-project_2"
        assert manifest.id_prefix == "POR_2"

    @pytest.mark.parametrize(
        "manifest",
        [
            {"schema": "backlog/Store@2", "project_id": "portable", "id_prefix": "POR"},
            {"schema": "backlog/Store@1", "project_id": "not valid", "id_prefix": "POR"},
            {"schema": "backlog/Store@1", "project_id": "portable", "id_prefix": "por"},
            {"schema": "backlog/Store@1", "project_id": "portable", "id_prefix": "POR", "extra": True},
        ],
    )
    def test_rejects_invalid_schema_identity_or_prefix(self, manifest):
        with pytest.raises(ValidationError):
            StoreManifest.model_validate(manifest)


class TestLoadStore:
    def test_loads_exact_store_into_immutable_context(self, tmp_path):
        root = _make_store(tmp_path)

        context = load_store(root)

        assert context.root == root.resolve()
        assert context.manifest.project_id == "portable-project"
        assert context.manifest_path == root / "backlog.json"
        assert context.items_path == root / "items"
        assert context.index_path == root / "INDEX.md"
        assert context.lock_path == root
        with pytest.raises(AttributeError):
            context.root = tmp_path  # type: ignore[misc]
        with pytest.raises(ValidationError):
            context.manifest.project_id = "other-project"  # type: ignore[misc]

    def test_relative_root_fails_without_creating_store(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        root = Path("portable-store")
        before = list(tmp_path.iterdir())

        with pytest.raises(StoreLoadError, match="must be absolute"):
            load_store(root)

        assert list(tmp_path.iterdir()) == before

    def test_missing_manifest_fails_without_creating_store_contents(self, tmp_path):
        root = tmp_path / "portable-store"
        root.mkdir()
        before = list(root.iterdir())

        with pytest.raises(StoreLoadError, match="manifest.*missing"):
            load_store(root)

        assert list(root.iterdir()) == before

    @pytest.mark.parametrize("entry_name", ["items", "INDEX.md"])
    def test_missing_required_entries_fail_without_changing_store(self, tmp_path, entry_name):
        root = _make_store(tmp_path)
        entry = root / entry_name
        if entry.is_dir():
            entry.rmdir()
        else:
            entry.unlink()
        before = _store_snapshot(root)

        with pytest.raises(StoreLoadError, match="missing"):
            load_store(root)

        assert _store_snapshot(root) == before

    @pytest.mark.parametrize(
        ("entry_name", "replacement"),
        [("items", "file"), ("INDEX.md", "directory")],
    )
    def test_wrong_required_entry_types_fail_without_changing_store(self, tmp_path, entry_name, replacement):
        root = _make_store(tmp_path)
        entry = root / entry_name
        if entry.is_dir():
            entry.rmdir()
        else:
            entry.unlink()
        if replacement == "file":
            entry.write_text("not a directory")
        else:
            entry.mkdir()
        before = _store_snapshot(root)

        with pytest.raises(StoreLoadError, match="regular|directory"):
            load_store(root)

        assert _store_snapshot(root) == before

    @pytest.mark.parametrize(
        "manifest_content",
        [
            "{not json}",
            json.dumps({"schema": "backlog/Store@2", "project_id": "portable", "id_prefix": "POR"}),
        ],
    )
    def test_malformed_or_unsupported_manifest_fails_explicitly(self, tmp_path, manifest_content):
        root = _make_store(tmp_path)
        (root / "backlog.json").write_text(manifest_content)

        with pytest.raises(StoreLoadError, match="manifest"):
            load_store(root)

    @pytest.mark.parametrize("entry_name", ["backlog.json", "items", "INDEX.md"])
    def test_rejects_static_symlink_escape(self, tmp_path, entry_name):
        root = _make_store(tmp_path)
        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / entry_name
        if entry_name == "items":
            target.mkdir()
        else:
            target.write_text("outside")
        entry = root / entry_name
        if entry.is_dir():
            entry.rmdir()
        else:
            entry.unlink()
        entry.symlink_to(target, target_is_directory=entry_name == "items")

        with pytest.raises(StoreLoadError, match="regular|directory"):
            load_store(root)

    def test_ignores_stale_regular_lock_residue(self, tmp_path):
        root = _make_store(tmp_path)
        (root / ".lock").write_text("stale residue")

        assert load_store(root).lock_path == root


class TestStoreContextReads:
    def test_empty_store_read_does_not_create_entries(self, tmp_path):
        root = _make_store(tmp_path)
        context = load_store(root)
        before = _store_snapshot(root)

        assert list_items(context) == []
        assert show_item("POR-001", context) is None

        assert _store_snapshot(root) == before

    def test_list_and_show_preserve_revision_and_effective_status(self, tmp_path):
        root = _make_store(tmp_path)
        _write_item(root, "POR-001", status="done", fixed_at="2026-08-01")
        _write_item(root, "POR-002", depends_on=["POR-001"])
        _write_item(root, "POR-003", depends_on=["POR-004"])
        context = load_store(root)

        items = list_items(context)
        blocked = show_item("POR-003", context)

        assert [item.id for item in items] == ["POR-001", "POR-002", "POR-003"]
        assert items[1].status == Status.TODO
        assert items[1].effective_status == Status.TODO
        assert blocked is not None
        assert blocked.revision
        assert blocked.status == Status.TODO
        assert blocked.effective_status == Status.BLOCKED

    def test_malformed_item_fails_strictly_without_writing(self, tmp_path):
        root = _make_store(tmp_path)
        malformed = root / "items" / "POR-001.md"
        malformed.write_text("not frontmatter", encoding="utf-8")
        context = load_store(root)
        before = _store_snapshot(root)

        with pytest.raises(BacklogItemParseError, match="POR-001.md"):
            list_items(context)

        assert _store_snapshot(root) == before

    def test_identical_relocated_store_has_deterministic_reads(self, tmp_path):
        root = _make_store(tmp_path)
        _write_item(root, "POR-002")
        _write_item(root, "POR-001")
        relocated = tmp_path / "relocated-store"
        shutil.copytree(root, relocated)

        original_items = list_items(load_store(root))
        relocated_items = list_items(load_store(relocated))

        assert [item.model_dump(mode="json") for item in relocated_items] == [
            item.model_dump(mode="json") for item in original_items
        ]

    def test_item_symlink_escape_fails_without_writing(self, tmp_path):
        root = _make_store(tmp_path)
        outside = tmp_path / "outside.md"
        outside.write_text("outside", encoding="utf-8")
        (root / "items" / "POR-001.md").symlink_to(outside)
        context = load_store(root)
        before = _store_snapshot(root)

        with pytest.raises(BacklogItemParseError, match="escapes store items directory"):
            list_items(context)
        with pytest.raises(BacklogItemParseError, match="escapes store items directory"):
            show_item("POR-001", context)

        assert _store_snapshot(root) == before

    def test_dangling_item_symlink_fails_without_writing(self, tmp_path):
        root = _make_store(tmp_path)
        (root / "items" / "POR-001.md").symlink_to(tmp_path / "missing.md")
        context = load_store(root)
        before = _store_snapshot(root)

        with pytest.raises(BacklogItemParseError, match="regular file"):
            list_items(context)
        with pytest.raises(BacklogItemParseError, match="regular file"):
            show_item("POR-001", context)

        assert _store_snapshot(root) == before


class TestExactStoreMutations:
    def _item(self, item_id: str = "AUTO", **overrides: object) -> BacklogItem:
        values = {
            "id": item_id,
            "project": "portable-project",
            "title": "Portable mutation",
            "category": Category.FEATURE,
            "priority": Priority.P1,
        }
        values.update(overrides)
        return BacklogItem.model_validate(values)

    def test_add_allocates_manifest_prefix_and_rebuilds_index(self, tmp_path):
        root = _make_store(tmp_path)
        store = load_store(root)

        filepath = add_item(self._item(), store)

        assert filepath == root / "items" / "POR-001.md"
        created = show_item("POR-001", store)
        assert created is not None
        assert created.project == "portable-project"
        assert "POR-001" in (root / "INDEX.md").read_text()

    @pytest.mark.parametrize(
        "item",
        [
            pytest.param(lambda self: self._item(project="another-project"), id="project-mismatch"),
            pytest.param(lambda self: self._item("OTHER-001"), id="id-prefix-mismatch"),
        ],
    )
    def test_add_rejects_identity_mismatch_without_mutating_store(self, tmp_path, item):
        root = _make_store(tmp_path)
        store = load_store(root)
        before = _store_snapshot(root)

        with pytest.raises(ValueError, match="manifest"):
            add_item(item(self), store)

        assert _store_snapshot(root) == before

    def test_update_rejects_item_identity_mismatch_without_mutating_store(self, tmp_path):
        root = _make_store(tmp_path)
        _write_item(root, "POR-001", project="another-project")
        store = load_store(root)
        before = _store_snapshot(root)

        with pytest.raises(ValueError, match="manifest"):
            update_item("POR-001", {"title": "Must not write"}, store)

        assert _store_snapshot(root) == before

    def test_update_rejects_project_patch_mismatch_without_mutating_store(self, tmp_path):
        root = _make_store(tmp_path)
        store = load_store(root)
        add_item(self._item(), store)
        before = _store_snapshot(root)

        with pytest.raises(ValueError, match="manifest"):
            update_item("POR-001", {"project": "another-project"}, store)

        assert _store_snapshot(root) == before

    def test_failed_dependency_and_revision_conflict_preserve_item_and_index(self, tmp_path):
        root = _make_store(tmp_path)
        store = load_store(root)
        add_item(self._item(), store)
        before = _store_snapshot(root)

        with pytest.raises(ValueError, match="Dependency not found"):
            update_item("POR-001", {"depends_on": ["POR-999"]}, store)
        assert _store_snapshot(root) == before

        with pytest.raises(ValueError, match="Revision mismatch"):
            update_item("POR-001", {"title": "Must not write"}, store, expected_revision="wrong")
        assert _store_snapshot(root) == before

    def test_add_never_clobbers_existing_item(self, tmp_path):
        root = _make_store(tmp_path)
        store = load_store(root)
        add_item(self._item("POR-001"), store)
        before = _store_snapshot(root)

        with pytest.raises(FileExistsError, match="already exists"):
            add_item(self._item("POR-001", title="Replacement"), store)

        assert _store_snapshot(root) == before

    def test_concurrent_auto_ids_are_unique(self, tmp_path):
        root = _make_store(tmp_path)
        store = load_store(root)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            created = list(executor.map(lambda _: add_item(self._item(), store), range(2)))

        assert {path.name for path in created} == {"POR-001.md", "POR-002.md"}
        assert [item.id for item in list_items(store)] == ["POR-001", "POR-002"]

    def test_generate_index_uses_exact_store_context(self, tmp_path):
        root = _make_store(tmp_path)
        store = load_store(root)
        add_item(self._item(), store)

        assert "POR-001" in generate_index(store)

    @pytest.mark.parametrize("temp_name", ["POR-001.tmp", "INDEX.tmp"])
    @pytest.mark.parametrize("target_kind", ["external", "dangling"])
    def test_temp_symlinks_fail_without_mutating_the_store(self, tmp_path, temp_name, target_kind):
        root = _make_store(tmp_path)
        store = load_store(root)
        temp_path = root / "items" / temp_name if temp_name.startswith("POR") else root / temp_name
        outside = tmp_path / "outside.tmp"
        if target_kind == "external":
            outside.write_text("outside")
        temp_path.symlink_to(outside)
        before = _store_snapshot(root)
        outside_before = outside.read_bytes() if outside.exists() else None

        with pytest.raises((ValueError, StoreLoadError), match="(?i)(temporary publish path|unsupported entry)"):
            add_item(self._item("POR-001"), store)

        assert _store_snapshot(root) == before
        assert (outside.read_bytes() if outside.exists() else None) == outside_before

    def test_persisted_auto_id_fails_without_mutating_store(self, tmp_path):
        root = _make_store(tmp_path)
        _write_item(root, "POR-001", id="AUTO")
        store = load_store(root)
        before = _store_snapshot(root)

        with pytest.raises(ValueError, match="AUTO"):
            update_item("POR-001", {"title": "Must not write"}, store)

        assert _store_snapshot(root) == before

    def test_filename_frontmatter_id_mismatch_fails_without_mutating_store(self, tmp_path):
        root = _make_store(tmp_path)
        _write_item(root, "POR-001", id="POR-002")
        store = load_store(root)
        before = _store_snapshot(root)

        with pytest.raises(ValueError, match="physical item ID"):
            update_item("POR-001", {"title": "Must not write"}, store)

        assert _store_snapshot(root) == before

    def test_independent_process_auto_adds_are_unique(self, tmp_path):
        root = _make_store(tmp_path)
        gate = tmp_path / "gate"
        ready_paths = [tmp_path / "ready-1", tmp_path / "ready-2"]
        child_code = """
import os
import time
from pathlib import Path
from backlog.items import add_item
from backlog.models import BacklogItem, Category, Priority
from backlog.store import load_store

Path(os.environ[\"READY\"]).write_text(\"ready\")
gate = Path(os.environ[\"GATE\"])
while not gate.exists():
    time.sleep(0.01)
store = load_store(Path(os.environ[\"STORE\"]))
item = BacklogItem(
    id=\"AUTO\", project=\"portable-project\", title=\"Concurrent\",
    category=Category.FEATURE, priority=Priority.P1,
)
print(add_item(item, store).name)
"""
        processes = []
        try:
            for ready_path in ready_paths:
                environment = os.environ | {
                    "STORE": str(root),
                    "GATE": str(gate),
                    "READY": str(ready_path),
                }
                processes.append(
                    subprocess.Popen(
                        [sys.executable, "-c", child_code],
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        env=environment,
                    )
                )
            deadline = time.monotonic() + 5
            while not all(path.exists() for path in ready_paths):
                assert time.monotonic() < deadline, "child processes did not reach the add barrier"
                time.sleep(0.01)
            gate.write_text("go")
            results = [process.communicate(timeout=5) for process in processes]
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()

        assert [stderr for _, stderr in results] == ["", ""]
        assert {stdout.strip() for stdout, _ in results} == {"POR-001.md", "POR-002.md"}
        assert [item.id for item in list_items(load_store(root))] == ["POR-001", "POR-002"]
        assert not (root / ".lock").exists()

    def test_directory_inode_lock_serializes_processes_without_store_entry(self, tmp_path):
        root = _make_store(tmp_path)
        ready = tmp_path / "ready"
        release = tmp_path / "release"
        holder_code = """
import os
import time
from pathlib import Path
from backlog.items import _lock_store
from backlog.store import load_store
store = load_store(Path(os.environ['STORE']))
with _lock_store(store):
    Path(os.environ['READY']).write_text(str(os.stat(store.root).st_ino))
    while not Path(os.environ['RELEASE']).exists():
        time.sleep(0.01)
"""
        waiter_code = """
import os
from pathlib import Path
from backlog.items import _lock_store
from backlog.store import load_store
store = load_store(Path(os.environ['STORE']))
with _lock_store(store):
    print(os.stat(store.root).st_ino, flush=True)
"""
        environment = os.environ | {"STORE": str(root), "READY": str(ready), "RELEASE": str(release)}
        holder = subprocess.Popen([sys.executable, "-c", holder_code], env=environment)
        waiter = None
        try:
            deadline = time.monotonic() + 5
            while not ready.exists():
                assert time.monotonic() < deadline, "holder did not acquire the directory lock"
                time.sleep(0.01)
            waiter = subprocess.Popen(
                [sys.executable, "-c", waiter_code], env=environment, stdout=subprocess.PIPE, text=True
            )
            time.sleep(0.1)
            assert waiter.poll() is None
            release.write_text("release")
            assert holder.wait(timeout=5) == 0
            stdout, _ = waiter.communicate(timeout=5)
        finally:
            if holder.poll() is None:
                holder.kill()
            if waiter is not None and waiter.poll() is None:
                waiter.kill()

        assert ready.read_text() == stdout.strip() == str(root.stat().st_ino)
        assert not (root / ".lock").exists()
