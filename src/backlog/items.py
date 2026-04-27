"""CRUD operations for backlog items."""

import os
from datetime import date, datetime
from pathlib import Path

import frontmatter
import yaml

from .models import BacklogItem, Category, Effort, Impact, Priority, Status

ITEMS_DIRNAME = "items"
INDEX_FILENAME = "INDEX.md"


def _find_backlog_dir(start: Path | None = None) -> Path | None:
    """Walk upward from start to find a docs/backlog/ directory."""
    if start is None:
        start = Path.cwd()
    for parent in [start, *start.parents]:
        candidate = parent / "docs" / "backlog"
        if candidate.is_dir():
            return candidate
    return None


def get_items_dir(project_path: Path | None = None) -> Path:
    """Return the items directory, creating it if needed."""
    base = _find_backlog_dir(project_path)
    if base is None:
        base = Path.cwd() / "docs" / "backlog"
    items_dir = base / ITEMS_DIRNAME
    items_dir.mkdir(parents=True, exist_ok=True)
    return items_dir


def _load_item(filepath: Path) -> BacklogItem | None:
    """Load a single backlog item from a markdown file."""
    try:
        post = frontmatter.load(filepath)
    except Exception:
        return None
    meta = dict(post.metadata)
    meta["id"] = meta.get("id", filepath.stem)
    meta.setdefault("body", post.content)
    # Handle date fields: both str and date accepted
    for field in ("created", "updated", "fixed_at"):
        val = meta.get(field)
        if isinstance(val, str):
            meta[field] = datetime.strptime(val, "%Y-%m-%d").date()
        elif isinstance(val, datetime):
            meta[field] = val.date()
        elif val is None and field != "fixed_at":
            meta[field] = date.today()
    return BacklogItem.model_validate(meta)


def list_items(project_path: Path | None = None) -> list[BacklogItem]:
    """List all backlog items."""
    items_dir = get_items_dir(project_path)
    items: list[BacklogItem] = []
    if not items_dir.exists():
        return items
    for f in sorted(items_dir.glob("*.md")):
        item = _load_item(f)
        if item is not None:
            items.append(item)
    return items


def show_item(item_id: str, project_path: Path | None = None) -> BacklogItem | None:
    """Show a single item by ID."""
    items_dir = get_items_dir(project_path)
    filepath = items_dir / f"{item_id}.md"
    if not filepath.exists():
        return None
    return _load_item(filepath)


def next_id(project_name: str, project_path: Path | None = None) -> str:
    """Generate the next sequential ID for a project."""
    prefix = project_name[:3].upper()
    items = list_items(project_path)
    existing = [
        int(item.id.split("-")[-1])
        for item in items
        if item.project == project_name and item.id.startswith(prefix)
    ]
    if not existing:
        return f"{prefix}-001"
    return f"{prefix}-{max(existing) + 1:03d}"


def add_item(item: BacklogItem, project_path: Path | None = None) -> Path:
    """Create a new backlog item file. Returns the file path."""
    items_dir = get_items_dir(project_path)
    metadata = item.model_dump(
        mode="json",
        exclude={"body"},
        exclude_none=True,
    )
    post = frontmatter.Post(item.body, **metadata)
    filepath = items_dir / f"{item.id}.md"
    filepath.write_text(frontmatter.dumps(post))
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
    item_id: str, updates: dict, project_path: Path | None = None
) -> BacklogItem | None:
    """Update a backlog item's frontmatter fields, preserving body."""
    items_dir = get_items_dir(project_path)
    filepath = items_dir / f"{item_id}.md"
    if not filepath.exists():
        return None

    current = show_item(item_id, project_path)
    if current is None:
        return None

    body = current.body
    current_data = current.model_dump(mode="json", exclude={"body"})

    for key, value in updates.items():
        if value is not None:
            current_data[key] = _jsonify(value)

    current_data["updated"] = date.today().isoformat()
    current_data.pop("body", None)

    post = frontmatter.Post(body, **current_data)
    filepath.write_text(frontmatter.dumps(post))
    return _load_item(filepath)


def generate_index(
    project_path: Path | None = None,
) -> str:
    """Generate an INDEX.md overview for the backlog."""
    items = list_items(project_path)
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
        by_status[item.status.value] = by_status.get(item.status.value, 0) + 1
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
    lines.append(f"| Priority | Count |")
    lines.append(f"|----------|-------|")
    for p in ("P0", "P1", "P2", "P3"):
        lines.append(f"| {p} | {by_priority.get(p, 0)} |")
    lines.append("")

    lines.append("## Recommended Next (by score)")
    lines.append("")
    active = [i for i in items if i.status in (Status.TODO, Status.IN_PROGRESS) and i.score > 0]
    for item in active[:20]:
        lines.append(
            f"- [{item.id}](items/{item.id}.md) [{item.priority.value}] "
            f"`{item.category.value}` {item.title} "
            f"_(effort: {item.effort.value}, impact: {item.impact.value})_"
        )

    return "\n".join(lines) + "\n"
