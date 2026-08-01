"""CRUD operations for backlog items."""

import contextlib
import fcntl
import json
import os
import re
import stat
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import frontmatter

from .models import BacklogItem, Status
from .store import StoreContext, StoreManifest, load_store

ITEMS_DIRNAME = "items"
INDEX_FILENAME = "INDEX.md"


class BacklogItemParseError(ValueError):
    """Raised when parsing or validating a backlog item file fails."""
    def __init__(self, filepath: Path, original_error: Exception):
        self.filepath = filepath
        self.original_error = original_error
        super().__init__(f"Failed to parse item file {filepath}: {original_error}")


class RevisionConflictError(ValueError):
    """Raised when a patch's optimistic revision does not match the current item."""


@dataclass(frozen=True, slots=True)
class PatchResult:
    """The complete outcome of one item patch."""

    before: BacklogItem
    result: BacklogItem
    changed_fields: list[str]
    no_op: bool


def _dump_post(post: frontmatter.Post) -> str:
    return frontmatter.dumps(post).rstrip("\n") + "\n"


def _find_backlog_dir(start: Path | None = None) -> Path | None:
    """Walk upward from start to find a docs/backlog/ directory."""
    if start is None:
        start = Path.cwd()
    for parent in [start, *start.parents]:
        candidate = parent / "docs" / "backlog"
        if candidate.is_dir():
            return candidate
    return None


def get_backlog_dir(project_path: Path | None = None, create: bool = False) -> Path:
    """Return the backlog directory. Prefer an existing direct backlog for Project Ops targets."""
    if project_path is not None:
        if project_path.name == "backlog":
            raise ValueError("--target must be the backlog store parent, not its backlog/ child")
        direct_backlog = project_path / "backlog"
        base = direct_backlog if direct_backlog.is_dir() else project_path / "docs" / "backlog"
    else:
        base = _find_backlog_dir(Path.cwd())
        if base is None:
            base = Path.cwd() / "docs" / "backlog"
    if create:
        base.mkdir(parents=True, exist_ok=True)
    return base


def get_items_dir(project_path: Path | None = None, create: bool = False) -> Path:
    """Return the items directory, optionally creating it."""
    base = get_backlog_dir(project_path, create=create)
    items_dir = base / ITEMS_DIRNAME
    if create:
        items_dir.mkdir(parents=True, exist_ok=True)
    return items_dir


def get_item_filepath(item_id: str, project_path: Path | None = None, create: bool = False) -> Path:
    """Get the resolved safe path for a backlog item. Raises ValueError if path traversal detected."""
    if not re.match(r"^[a-zA-Z0-9_-]+$", item_id):
        raise ValueError("Invalid item ID format")
    items_dir = get_items_dir(project_path, create=create)
    filepath = (items_dir / f"{item_id}.md").resolve()
    if not filepath.is_relative_to(items_dir.resolve()):
        raise ValueError("Path traversal detected")
    return filepath


def get_project_prefix(project_name: str) -> str:
    return project_name[:3].upper()


def check_dependencies(item_id: str, depends_on: list[str], store: StoreContext) -> None:
    """Validate dependency constraints: self-dependency, existence, and cycle detection."""
    if item_id in depends_on:
        raise ValueError(f"Self dependency detected: '{item_id}' cannot depend on itself.")

    all_items = list_items(store)
    existing_ids = {item.id for item in all_items}

    for dep in depends_on:
        if dep not in existing_ids:
            raise ValueError(f"Dependency not found: '{dep}' does not exist.")

    # Cycle detection
    graph: dict[str, list[str]] = {item.id: item.depends_on for item in all_items}
    graph[item_id] = depends_on

    visited: dict[str, int] = {node: 0 for node in graph}

    def dfs(node: str) -> bool:
        visited[node] = 1  # visiting
        for neighbor in graph.get(node, []):
            if neighbor not in visited:
                continue
            if visited[neighbor] == 1:
                return True
            if visited[neighbor] == 0 and dfs(neighbor):
                return True
        visited[node] = 2  # visited
        return False

    for node in graph:
        if visited[node] == 0 and dfs(node):
            raise ValueError("Circular dependency detected.")


_items_warnings: list[str] = []


def get_warnings() -> list[str]:
    """Get and clear all warnings collected in items module."""
    global _items_warnings
    w = list(_items_warnings)
    _items_warnings.clear()
    return w


def _rebuild_index_silent(store: StoreContext) -> None:
    """Silently rebuild the INDEX.md file, saving errors to warnings."""
    try:
        content = generate_index(store)
        index_path = store.index_path
        _atomic_publish_text(index_path, content)
    except Exception as e:
        _items_warnings.append(f"Failed to rebuild INDEX.md: {e}")


@contextlib.contextmanager
def _lock_store(store: StoreContext):
    """Acquire an advisory lock on the exact canonical store directory inode."""
    lock_directory = store.lock_path
    try:
        mode = lock_directory.lstat().st_mode
        resolved = lock_directory.resolve(strict=True)
    except OSError as error:
        raise ValueError(f"Cannot inspect store lock directory: {lock_directory}") from error
    if not stat.S_ISDIR(mode) or resolved != store.root:
        raise ValueError(f"Store lock directory must be the canonical store root: {lock_directory}")
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    except AttributeError as error:
        raise ValueError("Directory inode locking requires O_DIRECTORY and O_CLOEXEC") from error
    try:
        descriptor = os.open(lock_directory, flags)
    except OSError as error:
        raise ValueError(f"Cannot open store lock directory: {lock_directory}") from error
    try:
        opened = os.fstat(descriptor)
        expected = store.root.stat()
        if not stat.S_ISDIR(opened.st_mode) or (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino):
            raise ValueError("Store lock directory inode changed before lock acquisition.")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _refresh_store_authority_locked(store: StoreContext) -> StoreContext:
    """Reject a manifest that appeared or changed after a legacy context was resolved."""
    try:
        store.manifest_path.lstat()
    except FileNotFoundError:
        return store

    authoritative = load_store(store.root)
    if (
        authoritative.manifest != store.manifest
        or authoritative.manifest_path != store.manifest_path
        or authoritative.items_path != store.items_path
        or authoritative.index_path != store.index_path
        or authoritative.lock_path != store.lock_path
    ):
        raise ValueError("Store manifest changed while mutation was waiting for the store lock.")
    return authoritative


def _load_item(filepath: Path) -> BacklogItem:
    """Load a single backlog item from a markdown file. Raises BacklogItemParseError on failure."""
    try:
        post = frontmatter.load(str(filepath))
    except Exception as e:
        raise BacklogItemParseError(filepath, e) from e
    try:
        meta = dict(post.metadata)
        meta["id"] = meta.get("id", filepath.stem)
        meta.setdefault("body", post.content)
        if not meta.get("revision"):
            import hashlib
            meta["revision"] = hashlib.md5(filepath.read_bytes()).hexdigest()[:8]
        # Handle date fields: both str and date accepted
        for field in ("created", "updated", "fixed_at"):
            val = meta.get(field)
            if isinstance(val, str):
                meta[field] = datetime.strptime(val, "%Y-%m-%d").date()
            elif isinstance(val, datetime):
                meta[field] = val.date()
            elif val is None and field != "fixed_at":
                meta[field] = date.today()

        # Separate custom fields into extra
        known_fields = set(BacklogItem.model_fields.keys())
        known_fields.discard("extra")
        extra_data = {}
        for k in list(meta.keys()):
            if k not in known_fields and k != "body":
                extra_data[k] = meta.pop(k)
        meta["extra"] = extra_data

        return BacklogItem.model_validate(meta)
    except Exception as e:
        raise BacklogItemParseError(filepath, e) from e


def _apply_dependency_blocking(items: list[BacklogItem]) -> None:
    """Auto-mark items as blocked when any dependency is not done."""
    done_ids = {item.id for item in items if item.status == Status.DONE}
    for item in items:
        if item.status in (Status.DONE, Status.CANCELLED):
            continue
        if item.depends_on and not all(dep in done_ids for dep in item.depends_on):
            item.is_blocked = True


def _store_item_path(item_id: str, store: StoreContext) -> Path:
    """Return an existing regular item file contained by an exact store context."""
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", item_id):
        raise ValueError("Invalid item ID format")
    filepath = store.items_path / f"{item_id}.md"
    try:
        mode = filepath.lstat().st_mode
    except FileNotFoundError:
        return filepath
    except OSError as error:
        raise BacklogItemParseError(filepath, error) from error
    try:
        resolved = filepath.resolve(strict=True)
    except FileNotFoundError as error:
        raise BacklogItemParseError(filepath, ValueError("item must be a regular file")) from error
    except OSError as error:
        raise BacklogItemParseError(filepath, error) from error
    if not resolved.is_relative_to(store.items_path):
        raise BacklogItemParseError(filepath, ValueError("item escapes store items directory"))
    if not stat.S_ISREG(mode):
        raise BacklogItemParseError(filepath, ValueError("item must be a regular file"))
    return filepath


def list_items(store: StoreContext) -> list[BacklogItem]:
    """List items from one exact StoreContext without filesystem discovery or writes."""
    items: list[BacklogItem] = []
    for filepath in sorted(store.items_path.glob("*.md")):
        safe_path = _store_item_path(filepath.stem, store)
        if safe_path.exists():
            items.append(_load_item(safe_path))
    _apply_dependency_blocking(items)
    return items


def validate_store_items(store: StoreContext) -> list[BacklogItem]:
    """Validate every persisted item through the canonical parser without writing."""
    validated: list[BacklogItem] = []
    for filepath in sorted(store.items_path.iterdir()):
        if filepath.name == ".gitkeep":
            try:
                mode = filepath.lstat().st_mode
                resolved = filepath.resolve(strict=True)
            except OSError as error:
                raise BacklogItemParseError(filepath, error) from error
            if not stat.S_ISREG(mode) or not resolved.is_relative_to(store.items_path):
                raise BacklogItemParseError(filepath, ValueError("items placeholder must be a contained regular file"))
            continue
        if filepath.suffix != ".md" or filepath.name != f"{filepath.stem}.md":
            raise BacklogItemParseError(filepath, ValueError("item entry must be a Markdown file"))
        safe_path = _store_item_path(filepath.stem, store)
        item = _load_item(safe_path)
        if item.id != filepath.stem:
            raise ValueError(f"Item frontmatter ID '{item.id}' does not match physical item ID '{filepath.stem}'.")
        _validate_item_identity(item, store)
        validated.append(item)
    return validated


def show_item(item_id: str, store: StoreContext) -> BacklogItem | None:
    """Show an item from one exact StoreContext without filesystem discovery or writes."""
    try:
        filepath = _store_item_path(item_id, store)
    except BacklogItemParseError:
        raise
    except ValueError:
        return None
    if not filepath.exists():
        return None
    item = _load_item(filepath)
    if item.status not in (Status.DONE, Status.CANCELLED) and item.depends_on:
        done_ids = {candidate.id for candidate in list_items(store) if candidate.status == Status.DONE}
        if not all(dep in done_ids for dep in item.depends_on):
            item.is_blocked = True
    return item


def list_legacy_items(project_path: Path | None = None) -> list[BacklogItem]:
    """List items through the legacy project-path discovery adapter."""
    return list_items(_legacy_store_context(project_path, _legacy_project_name(project_path), create=False))


def show_legacy_item(item_id: str, project_path: Path | None = None) -> BacklogItem | None:
    """Show an item through the legacy project-path discovery adapter."""
    return show_item(item_id, _legacy_store_context(project_path, _legacy_project_name(project_path), create=False))


def _validate_item_identity(
    item: BacklogItem,
    store: StoreContext,
    *,
    allow_auto: bool = False,
) -> None:
    """Require persisted item identity to match the exact store manifest."""
    if item.project != store.manifest.project_id:
        raise ValueError(
            f"Item project '{item.project}' does not match store manifest project_id "
            f"'{store.manifest.project_id}'."
        )
    if item.id == "AUTO":
        if allow_auto:
            return
        raise ValueError("Persisted item ID cannot be AUTO.")
    if not item.id.startswith(f"{store.manifest.id_prefix}-"):
        raise ValueError(
            f"Item ID '{item.id}' does not match store manifest id_prefix "
            f"'{store.manifest.id_prefix}'."
        )


def next_id(store: StoreContext) -> str:
    """Allocate the next ID using only the exact store manifest prefix."""
    prefix = store.manifest.id_prefix
    sequence_pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
    existing = [
        int(match.group(1))
        for item in list_items(store)
        if (match := sequence_pattern.fullmatch(item.id)) is not None
    ]
    return f"{prefix}-{max(existing, default=0) + 1:03d}"


def _item_post(item: BacklogItem) -> frontmatter.Post:
    metadata = item.model_dump(
        mode="json",
        exclude={"body", "score", "effective_status"},
        exclude_none=True,
    )
    extra = metadata.pop("extra", {})
    if extra:
        metadata.update(extra)
    return frontmatter.Post(item.body, **metadata)


def _temporary_publish_path(filepath: Path) -> Path:
    return filepath.with_suffix(".tmp")


def _validate_temporary_publish_path(temp_filepath: Path) -> None:
    """Reject any pre-existing publication temp entry without following it."""
    try:
        mode = temp_filepath.lstat().st_mode
    except FileNotFoundError:
        return
    except OSError as error:
        raise ValueError(f"Cannot inspect temporary publish path: {temp_filepath}") from error
    if stat.S_ISLNK(mode):
        raise ValueError(f"Temporary publish path must not be a symlink: {temp_filepath}")
    if not stat.S_ISREG(mode):
        raise ValueError(f"Temporary publish path must be a regular file: {temp_filepath}")
    raise ValueError(f"Temporary publish path already exists: {temp_filepath}")


def _prepare_mutation_publication(item_filepath: Path, store: StoreContext) -> None:
    """Preflight all mutation temp paths before publishing either item or index."""
    _validate_temporary_publish_path(_temporary_publish_path(item_filepath))
    _validate_temporary_publish_path(_temporary_publish_path(store.index_path))


def _atomic_publish_text(filepath: Path, content: str) -> None:
    """Create a fresh contained temp file and atomically replace its final path."""
    temp_filepath = _temporary_publish_path(filepath)
    _validate_temporary_publish_path(temp_filepath)
    try:
        with temp_filepath.open("x", encoding="utf-8") as temp_file:
            temp_file.write(content)
    except FileExistsError as error:
        raise ValueError(f"Temporary publish path already exists: {temp_filepath}") from error
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            temp_filepath.unlink()
        raise
    try:
        temp_filepath.replace(filepath)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            temp_filepath.unlink()
        raise


def _replace_item(filepath: Path, item: BacklogItem) -> None:
    _atomic_publish_text(filepath, _dump_post(_item_post(item)))


def add_item(item: BacklogItem, store: StoreContext) -> Path:
    """Create one item through an exact StoreContext."""
    _validate_item_identity(item, store, allow_auto=True)
    with _lock_store(store):
        store = _refresh_store_authority_locked(store)
        if item.id == "AUTO":
            item.id = next_id(store)
        _validate_item_identity(item, store)
        if not item.revision:
            item.revision = uuid.uuid4().hex[:8]

        check_dependencies(item.id, item.depends_on, store)
        filepath = _store_item_path(item.id, store)
        if filepath.exists():
            raise FileExistsError(f"Backlog item with ID '{item.id}' already exists.")
        _prepare_mutation_publication(filepath, store)
        _replace_item(filepath, item)
        _rebuild_index_silent(store)
        return filepath


def _jsonify(value):
    """Convert enum/date values to JSON-safe primitives."""
    from enum import Enum
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, list):
        return [_jsonify(v) for v in value]
    return value


def patch_item(
    item_id: str,
    updates: dict,
    store: StoreContext,
    expected_revision: str | None = None,
) -> PatchResult | None:
    """Patch one item and return its complete mutation outcome."""
    if "id" in updates and updates["id"] != item_id:
        raise ValueError("Item ID cannot be changed.")
    if "project" in updates and updates["project"] != store.manifest.project_id:
        raise ValueError("Item project does not match store manifest project_id.")

    current = show_item(item_id, store)
    if current is None:
        return None
    _validate_item_identity(current, store)
    if current.id != item_id:
        raise ValueError(f"Item frontmatter ID '{current.id}' does not match physical item ID '{item_id}'.")

    with _lock_store(store):
        store = _refresh_store_authority_locked(store)
        current = show_item(item_id, store)
        if current is None:
            return None
        _validate_item_identity(current, store)
        if current.id != item_id:
            raise ValueError(f"Item frontmatter ID '{current.id}' does not match physical item ID '{item_id}'.")
        if expected_revision is not None and current.revision != expected_revision:
            raise RevisionConflictError(
                f"Revision mismatch: expected '{expected_revision}', but current is '{current.revision}'"
            )
        if "depends_on" in updates:
            check_dependencies(item_id, updates["depends_on"], store)

        current_data = current.model_dump(
            mode="json",
            exclude={"score", "effective_status"},
            exclude_none=True,
        )
        for key, value in updates.items():
            if value is not None:
                current_data[key] = _jsonify(value)
        if current_data.get("status") == Status.DONE.value:
            if current.status != Status.DONE or current.fixed_at is None:
                current_data["fixed_at"] = date.today().isoformat()
        else:
            current_data.pop("fixed_at", None)

        updated = BacklogItem.model_validate(current_data)
        _validate_item_identity(updated, store)
        compared_fields = set(current_data) | set(
            updated.model_dump(mode="json", exclude={"score", "effective_status"})
        )
        changed_fields = sorted(
            field
            for field in compared_fields - {"updated", "revision", "is_blocked", "extra"}
            if _jsonify(getattr(current, field, None)) != _jsonify(getattr(updated, field, None))
        )
        if not changed_fields:
            return PatchResult(before=current, result=current, changed_fields=[], no_op=True)

        current_data["updated"] = date.today().isoformat()
        current_data["revision"] = uuid.uuid4().hex[:8]
        updated = BacklogItem.model_validate(current_data)
        _validate_item_identity(updated, store)
        filepath = _store_item_path(item_id, store)
        _prepare_mutation_publication(filepath, store)
        _replace_item(filepath, updated)
        _rebuild_index_silent(store)
        result = show_item(item_id, store)
        if result is None:
            raise RuntimeError(f"Updated item '{item_id}' disappeared before it could be read.")
        return PatchResult(before=current, result=result, changed_fields=changed_fields, no_op=False)


def update_item(
    item_id: str,
    updates: dict,
    store: StoreContext,
    expected_revision: str | None = None,
) -> BacklogItem | None:
    """Compatibility wrapper returning only the patched item."""
    outcome = patch_item(item_id, updates, store, expected_revision=expected_revision)
    return outcome.result if outcome is not None else None


def _render_index(items: list[BacklogItem]) -> str:
    """Render an index from already-resolved items."""
    items.sort(key=lambda x: x.score, reverse=True)

    lines = [
        "# Backlog Index",
        "",
        f"> Auto-generated — {date.today().isoformat()}",
        f"> Total items: {len(items)}",
        "",
    ]

    # Stats
    by_status: dict[str, int] = {}
    by_category: dict[str, int] = {}
    by_priority: dict[str, int] = {}
    for item in items:
        by_status[item.effective_status.value] = by_status.get(item.effective_status.value, 0) + 1
        by_category[item.category.value] = by_category.get(item.category.value, 0) + 1
        by_priority[item.priority.value] = by_priority.get(item.priority.value, 0) + 1

    lines.append("## Status")
    lines.append("")
    for s in ("todo", "in_progress", "blocked", "done", "cancelled"):
        count = by_status.get(s, 0)
        icon = {"todo": "⬜", "in_progress": "🔄", "blocked": "🚫", "done": "✅", "cancelled": "❌"}.get(s, "")
        lines.append(f"- {icon} **{s}**: {count}")
    lines.append("")

    lines.append("## By Priority")
    lines.append("")
    lines.append("| Priority | Count |")
    lines.append("|----------|-------|")
    for p in ("P0", "P1", "P2", "P3"):
        lines.append(f"| {p} | {by_priority.get(p, 0)} |")
    lines.append("")

    lines.append("## Recommended Next (by score)")
    active = [i for i in items if i.effective_status in (Status.TODO, Status.IN_PROGRESS) and i.score > 0]
    if active:
        lines.append("")
    for item in active[:20]:
        lines.append(
            f"- [{item.id}](items/{item.id}.md) [{item.priority.value}] "
            f"`{item.category.value}` {item.title} "
            f"_(effort: {item.effort.value}, impact: {item.impact.value})_"
        )

    return "\n".join(lines) + "\n"


def generate_index(store: StoreContext) -> str:
    """Generate an INDEX.md overview using only an exact StoreContext."""
    return _render_index(list_items(store))


def _legacy_prefix(project_name: str, project_path: Path | None) -> str:
    """Apply historical directory/name prefix inference in the legacy adapter only."""
    prefix = get_project_prefix(project_name)
    items_dir = get_items_dir(project_path, create=False)
    if not items_dir.exists():
        return prefix
    prefixes = {
        item.id.split("-", 1)[0]
        for filepath in sorted(items_dir.glob("*.md"))
        if (item := _load_item(filepath)).project == project_name and "-" in item.id
    }
    return next(iter(prefixes)) if len(prefixes) == 1 else prefix


def _legacy_project_name(project_path: Path | None) -> str:
    """Preserve historical project-name inference at the CLI compatibility boundary."""
    if project_path is not None:
        return project_path.resolve().name
    backlog_dir = get_backlog_dir(None)
    if backlog_dir.is_dir() and backlog_dir.name == "backlog" and backlog_dir.parent.name == "docs":
        return backlog_dir.parent.parent.resolve().name
    return Path.cwd().resolve().name


def _legacy_store_context(
    project_path: Path | None,
    project_name: str | None,
    *,
    create: bool,
    id_prefix: str | None = None,
) -> StoreContext:
    """Resolve a historical target layout before entering exact mutation core."""
    project_name = project_name or _legacy_project_name(project_path)
    backlog_dir = get_backlog_dir(project_path, create=create)
    manifest_path = backlog_dir / "backlog.json"
    if manifest_path.exists() or manifest_path.is_symlink():
        return load_store(backlog_dir)
    items_path = get_items_dir(project_path, create=create)
    index_path = backlog_dir / INDEX_FILENAME
    manifest = StoreManifest.model_validate(
        {
            "schema": "backlog/Store@1",
            "project_id": project_name,
            "id_prefix": id_prefix or _legacy_prefix(project_name, project_path),
        }
    )
    return StoreContext(
        root=backlog_dir.resolve(),
        manifest=manifest,
        manifest_path=manifest_path,
        items_path=items_path.resolve(),
        index_path=index_path,
        lock_path=backlog_dir.resolve(),
    )


def next_legacy_id(project_name: str, project_path: Path | None = None) -> str:
    """Generate an ID through the legacy layout adapter."""
    return next_id(_legacy_store_context(project_path, project_name, create=False))


def add_legacy_item(item: BacklogItem, project_path: Path | None = None) -> Path:
    """Create an item through the historical target-layout adapter."""
    id_prefix = item.id.split("-", 1)[0] if item.id != "AUTO" and "-" in item.id else None
    return add_item(
        item,
        _legacy_store_context(project_path, item.project, create=True, id_prefix=id_prefix),
    )


def update_legacy_item(
    item_id: str,
    updates: dict,
    project_path: Path | None = None,
    expected_revision: str | None = None,
) -> BacklogItem | None:
    """Patch an item through the historical target-layout adapter."""
    current = show_legacy_item(item_id, project_path)
    if current is None:
        return None
    return update_item(
        item_id,
        updates,
        _legacy_store_context(
            project_path,
            current.project,
            create=True,
            id_prefix=current.id.split("-", 1)[0],
        ),
        expected_revision=expected_revision,
    )


def generate_legacy_index(
    project_path: Path | None = None,
    project: str | None = None,
) -> str:
    """Generate a historical-layout index without exposing discovery to core APIs."""
    items = list_legacy_items(project_path)
    if project:
        items = [item for item in items if item.project == project]
    return _render_index(items)


def provision_legacy_store(
    project_path: Path | None,
    *,
    project_id: str,
    id_prefix: str,
) -> StoreContext:
    """Create a manifest for one validated legacy store without replacing any manifest."""
    backlog_dir = get_backlog_dir(project_path, create=False)
    try:
        root_mode = backlog_dir.lstat().st_mode
    except FileNotFoundError as error:
        raise ValueError(f"Legacy store root is missing: {backlog_dir}") from error
    if not stat.S_ISDIR(root_mode):
        raise ValueError(f"Legacy store root must be a directory: {backlog_dir}")

    root = backlog_dir.resolve(strict=True)
    items_path = root / ITEMS_DIRNAME
    index_path = root / INDEX_FILENAME
    manifest_path = root / "backlog.json"
    manifest = StoreManifest.model_validate(
        {"schema": "backlog/Store@1", "project_id": project_id, "id_prefix": id_prefix}
    )
    store = StoreContext(
        root=root,
        manifest=manifest,
        manifest_path=manifest_path,
        items_path=items_path,
        index_path=index_path,
        lock_path=root,
    )
    with _lock_store(store):
        for path, description, expected_mode in (
            (items_path, "items directory", stat.S_ISDIR),
            (index_path, "index", stat.S_ISREG),
        ):
            try:
                mode = path.lstat().st_mode
            except FileNotFoundError as error:
                raise ValueError(f"Legacy {description} is missing: {path}") from error
            if not expected_mode(mode):
                raise ValueError(f"Legacy {description} has an invalid type: {path}")
            if not path.resolve(strict=True).is_relative_to(root):
                raise ValueError(f"Legacy {description} escapes store root: {path}")
        try:
            manifest_path.lstat()
        except FileNotFoundError:
            pass
        else:
            raise ValueError(f"Store manifest already exists and will not be overwritten: {manifest_path}")
        validate_store_items(store)

        temp_path = root / f".backlog.json.{uuid.uuid4().hex}.tmp"
        temp_created = False
        try:
            with temp_path.open("x", encoding="utf-8") as temp_file:
                temp_created = True
                json.dump(manifest.model_dump(by_alias=True), temp_file, indent=2)
                temp_file.write("\n")
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.link(temp_path, manifest_path)
        except FileExistsError as error:
            raise ValueError(f"Store manifest already exists and will not be overwritten: {manifest_path}") from error
        except OSError as error:
            raise ValueError(f"Cannot publish store manifest without overwrite: {manifest_path}") from error
        finally:
            if temp_created:
                with contextlib.suppress(FileNotFoundError):
                    temp_path.unlink()
        return load_store(root)


def validate_legacy_store(
    project_path: Path | None,
    *,
    project_id: str,
    id_prefix: str,
) -> StoreContext:
    """Validate one manifest-less legacy store through the portable item parser without writing."""
    backlog_dir = get_backlog_dir(project_path, create=False)
    try:
        root_mode = backlog_dir.lstat().st_mode
    except FileNotFoundError as error:
        raise ValueError(f"Legacy store root is missing: {backlog_dir}") from error
    if not stat.S_ISDIR(root_mode):
        raise ValueError(f"Legacy store root must be a directory: {backlog_dir}")
    root = backlog_dir.resolve(strict=True)
    items_path = root / ITEMS_DIRNAME
    index_path = root / INDEX_FILENAME
    manifest_path = root / "backlog.json"
    if manifest_path.exists() or manifest_path.is_symlink():
        raise ValueError(f"Legacy store manifest already exists: {manifest_path}")
    for path, description, expected_mode in (
        (items_path, "items directory", stat.S_ISDIR),
        (index_path, "index", stat.S_ISREG),
    ):
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError as error:
            raise ValueError(f"Legacy {description} is missing: {path}") from error
        if not expected_mode(mode):
            raise ValueError(f"Legacy {description} has an invalid type: {path}")
        if not path.resolve(strict=True).is_relative_to(root):
            raise ValueError(f"Legacy {description} escapes store root: {path}")
    store = StoreContext(
        root=root,
        manifest=StoreManifest.model_validate(
            {"schema": "backlog/Store@1", "project_id": project_id, "id_prefix": id_prefix}
        ),
        manifest_path=manifest_path,
        items_path=items_path,
        index_path=index_path,
        lock_path=root,
    )
    validate_store_items(store)
    return store
