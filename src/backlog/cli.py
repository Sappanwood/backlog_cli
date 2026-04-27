"""CLI entry point using typer and rich."""

import os
from datetime import date
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from .items import add_item, generate_index, list_items, next_id, show_item, update_item
from .models import (
    BacklogItem,
    Category,
    Effort,
    Impact,
    Priority,
    Status,
)

app = typer.Typer(help="Unified backlog manager")
console = Console()
_project_dir: Path | None = None


@app.callback()
def main(
    dir: Annotated[
        Path | None,
        typer.Option("--dir", "-d", help="Project directory (default: current dir)"),
    ] = None,
):
    global _project_dir
    _project_dir = dir


def _project_path() -> Path:
    return _project_dir or Path.cwd()


def _print_table(items: list[BacklogItem], show_score: bool = False) -> None:
    if not items:
        console.print("[dim]No items found.[/dim]")
        return

    columns = [
        ("ID", "cyan"),
        ("Status", "dim"),
        ("Pri", "red"),
        ("Title", "white"),
        ("Category", "yellow"),
        ("Effort", "green"),
        ("Impact", "blue"),
    ]
    if show_score:
        columns.insert(-1, ("Score", "magenta"))

    table = Table(show_header=True, header_style="bold")
    for name, _ in columns:
        table.add_column(name)

    for item in items:
        status_icon = {
            Status.TODO: "⬜",
            Status.IN_PROGRESS: "🔄",
            Status.BLOCKED: "🚫",
            Status.DONE: "✅",
            Status.CANCELLED: "❌",
        }.get(item.status, "?")

        row = [
            item.id,
            f"{status_icon} {item.status.value}",
            item.priority.value,
            item.title[:60],
            item.category.value,
            item.effort.value,
            item.impact.value,
        ]
        if show_score:
            row.insert(-1, str(int(item.score)))
        table.add_row(*row)

    console.print(table)
    console.print(f"[dim]Total: {len(items)} items[/dim]")


@app.command(name="list")
def list_cmd(
    project: str | None = typer.Option(None, "--project", "-p", help="Filter by project"),
    category: Category | None = typer.Option(None, "--category", "-c", help="Filter by category"),
    priority: str | None = typer.Option(None, "--priority", help="Filter by priority (comma-separated, e.g. P0,P1)"),
    status: Status | None = typer.Option(None, "--status", "-s", help="Filter by status"),
    tag: str | None = typer.Option(None, "--tag", "-t", help="Filter by tag"),
    sort: str = typer.Option("score", "--sort", help="Sort by: score, priority, id"),
    limit: int = typer.Option(0, "--limit", "-n", help="Limit results"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """List backlog items with optional filters."""
    items = list_items(_project_path())

    if project:
        items = [i for i in items if i.project == project]
    if category:
        items = [i for i in items if i.category == category]
    if priority:
        allowed = set(p.strip() for p in priority.split(","))
        items = [i for i in items if i.priority.value in allowed]
    if status:
        items = [i for i in items if i.status == status]
    if tag:
        items = [i for i in items if tag in i.tags]

    if sort == "score":
        items.sort(key=lambda x: x.score, reverse=True)
    elif sort == "priority":
        items.sort(key=lambda x: (x.priority.value, x.score), reverse=False)
    elif sort == "id":
        items.sort(key=lambda x: x.id)

    if limit > 0:
        items = items[:limit]

    if json_output:
        import json
        console.print(json.dumps([i.model_dump(mode="json") for i in items], indent=2))
    else:
        _print_table(items, show_score=(sort == "score"))


@app.command()
def show(
    item_id: str = typer.Argument(..., help="Item ID to show"),
):
    """Show full details of a backlog item."""
    item = show_item(item_id, _project_path())
    if item is None:
        console.print(f"[red]Item '{item_id}' not found.[/red]")
        raise typer.Exit(1)

    console.print(
        f"[bold cyan]{item.id}[/bold cyan]  [red]{item.priority.value}[/red]  "
        f"[yellow]{item.category.value}[/yellow]"
    )
    console.print(f"[bold white]{item.title}[/bold white]")
    console.print()
    console.print(f"Status: [bold]{item.status.value}[/bold]")
    console.print(f"Effort: {item.effort.value}  |  Impact: {item.impact.value}  |  Score: {int(item.score)}")
    console.print(f"Project: {item.project}  |  Source: {item.source or '-'}")
    if item.tags:
        console.print(f"Tags: {', '.join(item.tags)}")
    if item.depends_on:
        all_items = {i.id: i for i in list_items(_project_path())}
        dep_statuses = []
        has_blocking = False
        for dep_id in item.depends_on:
            dep = all_items.get(dep_id)
            if dep is None:
                dep_statuses.append(f"[red]{dep_id} (missing)[/red]")
                has_blocking = True
            elif dep.status == Status.DONE:
                dep_statuses.append(f"[green]{dep_id} ({dep.status.value})[/green]")
            else:
                dep_statuses.append(f"[red]{dep_id} ({dep.status.value})[/red]")
                has_blocking = True
        console.print(f"Depends on: {', '.join(dep_statuses)}")
        if item.status == Status.BLOCKED and has_blocking:
            console.print("[yellow]Blocked: some dependencies are not done[/yellow]")
    console.print(f"Created: {item.created}  |  Updated: {item.updated}")
    if item.fixed_at:
        console.print(f"Fixed at: {item.fixed_at}")
    if item.body.strip():
        console.print()
        console.print(item.body)


@app.command()
def add(
    project: str = typer.Option(..., "--project", "-p", help="Project name (inkborn, zhijian)"),
    title: str = typer.Option(..., "--title", "-t", help="Item title"),
    category: Category = typer.Option(..., "--category", "-c", help="Category"),
    priority: Priority = typer.Option(..., "--priority", help="Priority (P0-P3)"),
    effort: Effort = typer.Option(Effort.M, "--effort", "-e", help="Effort estimate"),
    impact: Impact = typer.Option(Impact.MEDIUM, "--impact", "-i", help="Impact level"),
    tags: str = typer.Option("", "--tags", help="Comma-separated tags"),
    source: str = typer.Option("", "--source", help="Source label"),
    depends_on: str = typer.Option("", "--depends-on", help="Comma-separated dependency IDs"),
    body: str = typer.Option("", "--body", "-b", help="Description body (markdown)"),
):
    """Add a new backlog item."""
    item_id = next_id(project, _project_path())
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    dep_list = [d.strip() for d in depends_on.split(",") if d.strip()]

    item = BacklogItem(
        id=item_id,
        project=project,
        title=title,
        category=category,
        priority=priority,
        effort=effort,
        impact=impact,
        tags=tag_list,
        source=source,
        depends_on=dep_list,
        body=body,
    )
    filepath = add_item(item, _project_path())
    console.print(f"[green]Created[/green] {item_id} → {filepath}")
    console.print(f"[bold white]{title}[/bold white]")


@app.command()
def update(
    item_id: str = typer.Argument(..., help="Item ID to update"),
    title: str | None = typer.Option(None, "--title", help="New title"),
    category: Category | None = typer.Option(None, "--category", "-c", help="New category"),
    priority: Priority | None = typer.Option(None, "--priority", help="New priority"),
    effort: Effort | None = typer.Option(None, "--effort", "-e", help="New effort"),
    impact: Impact | None = typer.Option(None, "--impact", "-i", help="New impact"),
    status: Status | None = typer.Option(None, "--status", "-s", help="New status"),
    tags: str | None = typer.Option(None, "--tags", help="Comma-separated tags (replaces)"),
    source: str | None = typer.Option(None, "--source", help="New source label"),
    fixed: bool = typer.Option(False, "--fixed", "-f", help="Mark as done with today's date (shorthand)"),
    body: str | None = typer.Option(None, "--body", "-b", help="New body text"),
):
    """Update a backlog item."""
    updates: dict = {}

    if title is not None:
        updates["title"] = title
    if category is not None:
        updates["category"] = category
    if priority is not None:
        updates["priority"] = priority
    if effort is not None:
        updates["effort"] = effort
    if impact is not None:
        updates["impact"] = impact
    if status is not None:
        updates["status"] = status
    if tags is not None:
        updates["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
    if source is not None:
        updates["source"] = source
    if body is not None:
        updates["body"] = body
    if fixed:
        updates["status"] = Status.DONE
        updates["fixed_at"] = date.today()
        updates.pop("fixed", None)

    if not updates:
        console.print("[yellow]No updates specified.[/yellow]")
        raise typer.Exit(1)

    result = update_item(item_id, updates, _project_path())
    if result is None:
        console.print(f"[red]Item '{item_id}' not found.[/red]")
        raise typer.Exit(1)
    console.print(f"[green]Updated[/green] {item_id} — {result.title}")


@app.command()
def stats(
    project: str | None = typer.Option(None, "--project", "-p", help="Filter by project"),
):
    """Show backlog statistics."""
    items = list_items(_project_path())
    if project:
        items = [i for i in items if i.project == project]

    if not items:
        console.print("[dim]No items.[/dim]")
        return

    by_status: dict[str, int] = {}
    by_category: dict[str, int] = {}
    by_priority: dict[str, int] = {}
    for item in items:
        by_status[item.status.value] = by_status.get(item.status.value, 0) + 1
        by_category[item.category.value] = by_category.get(item.category.value, 0) + 1
        by_priority[item.priority.value] = by_priority.get(item.priority.value, 0) + 1

    total = len(items)
    active = sum(1 for i in items if i.status in (Status.TODO, Status.IN_PROGRESS, Status.BLOCKED))
    done = sum(1 for i in items if i.status == Status.DONE)

    console.print(f"[bold]Total:[/bold] {total}  |  [bold]Active:[/bold] {active}  |  [bold]Done:[/bold] {done}")
    console.print()

    # Status table
    status_table = Table(title="By Status", show_header=True)
    status_table.add_column("Status")
    status_table.add_column("Count")
    status_table.add_column("%")
    for s in ("todo", "in_progress", "blocked", "done", "cancelled"):
        count = by_status.get(s, 0)
        pct = f"{count / total * 100:.0f}%" if total > 0 else "-"
        status_table.add_row(s, str(count), pct)
    console.print(status_table)

    console.print()

    # Priority table
    pri_table = Table(title="By Priority", show_header=True)
    pri_table.add_column("Priority")
    pri_table.add_column("Count")
    for p in ("P0", "P1", "P2", "P3"):
        pri_table.add_row(p, str(by_priority.get(p, 0)))
    console.print(pri_table)

    console.print()

    # Category table
    cat_table = Table(title="By Category", show_header=True)
    cat_table.add_column("Category")
    cat_table.add_column("Count")
    for c in sorted(by_category, key=lambda x: by_category[x], reverse=True):
        cat_table.add_row(c, str(by_category[c]))
    console.print(cat_table)


@app.command(name="next")
def next_cmd(
    project: str | None = typer.Option(None, "--project", "-p", help="Filter by project"),
    limit: int = typer.Option(5, "--limit", "-n", help="Number of items to show"),
):
    """Show recommended next items sorted by priority score."""
    items = list_items(_project_path())
    if project:
        items = [i for i in items if i.project == project]

    active = [i for i in items if i.status in (Status.TODO, Status.IN_PROGRESS) and i.score > 0]
    active.sort(key=lambda x: x.score, reverse=True)
    active = active[:limit]

    if not active:
        console.print("[dim]No active items to recommend.[/dim]")
        return

    _print_table(active, show_score=True)


@app.command()
def index(
    project: str | None = typer.Option(None, "--project", "-p", help="Filter by project"),
):
    """Generate docs/backlog/INDEX.md overview."""
    content = generate_index(_project_path())
    index_path = _project_path() / "docs" / "backlog" / "INDEX.md"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(content)
    console.print(f"[green]Generated[/green] {index_path}")


@app.command()
def edit(
    item_id: str = typer.Argument(..., help="Item ID to edit"),
):
    """Open a backlog item in $EDITOR."""
    items_dir = _project_path() / "docs" / "backlog" / "items"
    filepath = items_dir / f"{item_id}.md"
    if not filepath.exists():
        console.print(f"[red]Item '{item_id}' not found.[/red]")
        raise typer.Exit(1)
    editor = os.environ.get("EDITOR", "vim")
    os.system(f"{editor} {filepath}")


if __name__ == "__main__":
    app()
