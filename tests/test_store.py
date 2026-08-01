"""Tests for the portable Backlog Store contract."""

import json
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from backlog.items import BacklogItemParseError, list_items, show_item
from backlog.models import Status
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


def _store_snapshot(root: Path) -> list[tuple[str, int, bytes | None]]:
    """Capture direct store entries to prove failed loading did not mutate them."""
    entries = []
    for entry in sorted(root.iterdir()):
        mode = entry.lstat().st_mode
        content = entry.read_bytes() if entry.is_file() and not entry.is_symlink() else None
        entries.append((entry.name, mode, content))
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
        assert context.lock_path == root / ".lock"
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

    def test_rejects_existing_symlink_lock(self, tmp_path):
        root = _make_store(tmp_path)
        outside_lock = tmp_path / "outside.lock"
        outside_lock.write_text("")
        (root / ".lock").symlink_to(outside_lock)

        with pytest.raises(StoreLoadError, match="lock.*regular"):
            load_store(root)


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
