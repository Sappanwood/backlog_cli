"""CRUD operations for backlog items."""

import contextlib
import fcntl
import json
import re
import uuid
from datetime import date, datetime
from pathlib import Path

import frontmatter

from .models import BacklogItem, Status

ITEMS_DIRNAME = "items"
INDEX_FILENAME = "INDEX.md"


class BacklogItemParseError(ValueError):
    """Raised when parsing or validating a backlog item file fails."""
    def __init__(self, filepath: Path, original_error: Exception):
        self.filepath = filepath
        self.original_error = original_error
        super().__init__(f"Failed to parse item file {filepath}: {original_error}")


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
    """Return the docs/backlog directory. Does not walk upward if project_path is explicitly provided."""
    if project_path is not None:
        base = project_path / "docs" / "backlog"
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
    """Get project backlog prefix from ~/.config/opencode/projects.json or fallback to project_name[:3].upper()."""
    registry_path = Path("~/.config/opencode/projects.json").expanduser()
    if registry_path.exists():
        try:
            with open(registry_path) as f:
                data = json.load(f)
            projects = data.get("projects", {})
            if project_name in projects:
                prefix = projects[project_name].get("backlog_prefix")
                if prefix:
                    return prefix.upper()
        except Exception:
            pass
    return project_name[:3].upper()


def check_dependencies(
    item_id: str,
    depends_on: list[str],
    project_path: Path | None = None,
) -> None:
    """Validate dependency constraints: self-dependency, existence, and cycle detection."""
    if item_id in depends_on:
        raise ValueError(f"Self dependency detected: '{item_id}' cannot depend on itself.")

    all_items = list_items(project_path)
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


def _rebuild_index_silent(project_path: Path | None = None) -> None:
    """Silently rebuild the INDEX.md file, saving errors to warnings."""
    try:
        content = generate_index(project_path)
        backlog_dir = get_backlog_dir(project_path, create=True)
        index_path = backlog_dir / INDEX_FILENAME
        temp_index_path = index_path.with_suffix(".tmp")
        temp_index_path.write_text(content)
        temp_index_path.replace(index_path)
    except Exception as e:
        _items_warnings.append(f"Failed to rebuild INDEX.md: {e}")


@contextlib.contextmanager
def _lock_backlog(project_path: Path | None = None):
    """Acquire an exclusive lock on the backlog directory using a lock file."""
    backlog_dir = get_backlog_dir(project_path, create=True)
    lock_file_path = backlog_dir / ".lock"
    with open(lock_file_path, "w") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


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


def list_items(project_path: Path | None = None) -> list[BacklogItem]:
    """List all backlog items. Auto-marks items as blocked if dependencies are not done."""
    items_dir = get_items_dir(project_path, create=False)
    items: list[BacklogItem] = []
    if not items_dir.exists():
        return items
    for f in sorted(items_dir.glob("*.md")):
        item = _load_item(f)
        items.append(item)
    _apply_dependency_blocking(items)
    return items


def show_item(item_id: str, project_path: Path | None = None) -> BacklogItem | None:
    """Show a single item by ID. Auto-marks as blocked if dependencies are not done."""
    try:
        filepath = get_item_filepath(item_id, project_path, create=False)
    except ValueError:
        return None
    if not filepath.exists():
        return None
    item = _load_item(filepath)
    if item.status not in (Status.DONE, Status.CANCELLED) and item.depends_on:
        done_ids = {
            i.id
            for i in list_items(project_path)
            if i.status == Status.DONE
        }
        if not all(dep in done_ids for dep in item.depends_on):
            item.is_blocked = True
    return item


def next_id(project_name: str, project_path: Path | None = None) -> str:
    """Generate the next sequential ID for a project, based on prefix matching with compatibility logic."""
    if not re.match(r"^[a-zA-Z0-9_-]+$", project_name):
        raise ValueError("Invalid project name format")
    prefix = get_project_prefix(project_name)
    items = list_items(project_path)
    
    # Extract prefixes of existing items belonging to this project
    project_items = [item for item in items if item.project == project_name]
    prefixes = set()
    for item in project_items:
        parts = item.id.split("-")
        if len(parts) > 1:
            prefixes.add(parts[0])
            
    # If there is exactly one existing prefix, reuse it to maintain continuity
    if len(prefixes) == 1:
        prefix = list(prefixes)[0]

    existing = [
        int(item.id.split("-")[-1])
        for item in items
        if item.id.startswith(f"{prefix}-")
    ]
    if not existing:
        return f"{prefix}-001"
    return f"{prefix}-{max(existing) + 1:03d}"


def add_item(item: BacklogItem, project_path: Path | None = None) -> Path:
    """Create a new backlog item file. Returns the file path."""
    with _lock_backlog(project_path):
        if item.id == "AUTO":
            item.id = next_id(item.project, project_path)
        if not item.revision:
            item.revision = uuid.uuid4().hex[:8]

        check_dependencies(item.id, item.depends_on, project_path)
        filepath = get_item_filepath(item.id, project_path, create=True)
        if filepath.exists():
            raise FileExistsError(f"Backlog item with ID '{item.id}' already exists.")
        metadata = item.model_dump(
            mode="json",
            exclude={"body", "score", "effective_status"},
            exclude_none=True,
        )
        extra = metadata.pop("extra", {})
        if extra:
            metadata.update(extra)
        post = frontmatter.Post(item.body, **metadata)
        
        temp_filepath = filepath.with_suffix(".tmp")
        temp_filepath.write_text(frontmatter.dumps(post))
        temp_filepath.replace(filepath)
        _rebuild_index_silent(project_path)
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


def update_item(
    item_id: str, updates: dict, project_path: Path | None = None, expected_revision: str | None = None
) -> BacklogItem | None:
    """Update a backlog item's frontmatter fields, preserving body. Supports expected_revision."""
    with _lock_backlog(project_path):
        if "depends_on" in updates:
            check_dependencies(item_id, updates["depends_on"], project_path)
        filepath = get_item_filepath(item_id, project_path, create=False)
        if not filepath.exists():
            return None

        current = show_item(item_id, project_path)
        if current is None:
            return None

        if expected_revision is not None and current.revision != expected_revision:
            raise ValueError(f"Revision mismatch: expected '{expected_revision}', but current is '{current.revision}'")

        body = current.body
        current_data = current.model_dump(mode="json", exclude={"body", "score", "effective_status"}, exclude_none=True)

        for key, value in updates.items():
            if value is not None:
                current_data[key] = _jsonify(value)

        if current_data.get("status") != Status.DONE.value:
            current_data.pop("fixed_at", None)

        current_data["updated"] = date.today().isoformat()
        current_data["revision"] = uuid.uuid4().hex[:8]

        extra = current_data.pop("extra", {})
        if extra:
            current_data.update(extra)

        body = current_data.pop("body", body)

        post = frontmatter.Post(body, **current_data)
        
        temp_filepath = filepath.with_suffix(".tmp")
        temp_filepath.write_text(frontmatter.dumps(post))
        temp_filepath.replace(filepath)
        _rebuild_index_silent(project_path)
        return show_item(item_id, project_path)


def generate_index(
    project_path: Path | None = None,
    project: str | None = None,
) -> str:
    """Generate an INDEX.md overview for the backlog."""
    items = list_items(project_path)
    if project:
        items = [i for i in items if i.project == project]
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
    lines.append("")
    active = [i for i in items if i.effective_status in (Status.TODO, Status.IN_PROGRESS) and i.score > 0]
    for item in active[:20]:
        lines.append(
            f"- [{item.id}](items/{item.id}.md) [{item.priority.value}] "
            f"`{item.category.value}` {item.title} "
            f"_(effort: {item.effort.value}, impact: {item.impact.value})_"
        )

    return "\n".join(lines) + "\n"
