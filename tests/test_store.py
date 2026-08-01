"""Tests for the portable Backlog Store contract."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

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
