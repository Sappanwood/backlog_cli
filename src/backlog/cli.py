"""CLI entry point using typer and rich."""

import os
import sys
from datetime import date
from pathlib import Path
from typing import Annotated

import click
import typer
from rich.console import Console
from rich.table import Table

from .items import (
    INDEX_FILENAME,
    BacklogItemParseError,
    add_item,
    generate_index,
    get_backlog_dir,
    get_item_filepath,
    get_warnings,
    update_item,
)
from .items import (
    list_legacy_items as list_items,
)
from .items import (
    show_legacy_item as show_item,
)
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
stderr_console = Console(stderr=True)
_target_path: Path | None = None


def _print_json_success(data: dict | list, warnings: list[str] | None = None) -> None:
    import json
    output = {
        "ok": True,
        "data": data,
    }
    if warnings is not None:
        output["warnings"] = warnings
    print(json.dumps(output, indent=2, ensure_ascii=False))


def _print_json_error(code: str, message: str, details: dict | None = None) -> None:
    import json
    output = {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
        }
    }
    print(json.dumps(output, indent=2, ensure_ascii=False))


@app.callback()
def main(
    target: Annotated[
        Path | None,
        typer.Option("--target", help="Project root directory (default: auto-detect from cwd)"),
    ] = None,
):
    global _target_path
    _target_path = target.resolve() if target else None


def _resolve_target_path() -> Path:
    return _target_path or Path.cwd()


def _resolve_project_name() -> str:
    return _resolve_target_path().resolve().name


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
        }.get(item.effective_status, "?")

        row = [
            item.id,
            f"{status_icon} {item.effective_status.value}",
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


def _print_csv(items: list[BacklogItem]) -> None:
    import csv
    import sys
    writer = csv.writer(sys.stdout)
    writer.writerow(["id", "status", "priority", "title", "category", "effort", "impact", "score"])
    for item in items:
        writer.writerow([
            item.id,
            item.effective_status.value,
            item.priority.value,
            item.title,
            item.category.value,
            item.effort.value,
            item.impact.value,
            int(item.score),
        ])


def _output_items(items: list[BacklogItem], format: str, show_score: bool = False) -> None:
    """Unified output: JSON, CSV, or Rich table."""
    if format == "json":
        _print_json_success([i.model_dump(mode="json") for i in items])
    elif format == "csv":
        _print_csv(items)
    else:
        _print_table(items, show_score=show_score)


def _resolve_body(body_str: str | None, body_file: Path | None, stdin: bool) -> str | None:
    """Resolve body from one of three mutually exclusive sources.  Returns None when no source is specified."""
    sources = sum([body_str is not None, body_file is not None, stdin])
    if sources > 1:
        raise typer.BadParameter("Only one of --body / -b, --body-file, --stdin may be used")
    if stdin:
        return sys.stdin.read()
    if body_file is not None:
        return body_file.read_text()
    return body_str


def _parse_csv_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


@app.command(name="list")
def list_cmd(
    category: Category | None = typer.Option(None, "--category", "-c", help="Filter by category"),
    priority: str | None = typer.Option(None, "--priority", help="Filter by priority (comma-separated, e.g. P0,P1)"),
    status: Status | None = typer.Option(None, "--status", "-s", help="Filter by status"),
    tag: str | None = typer.Option(None, "--tag", "-t", help="Filter by tag"),
    sort: str = typer.Option("score", "--sort", help="Sort by: score, priority, id"),
    limit: int = typer.Option(0, "--limit", "-n", help="Limit results"),
    format: str = typer.Option("table", "--format", help="Output format: json, table, csv"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (deprecated)"),
):
    """List backlog items with optional filters."""
    if format not in ("json", "table", "csv"):
        raise typer.BadParameter("Format must be one of: json, table, csv")
    if json_output:
        format = "json"

    if sort not in ("score", "priority", "id"):
        raise typer.BadParameter("Sort option must be one of: score, priority, id")

    if limit < 0:
        raise typer.BadParameter("Limit must be a non-negative integer.")

    allowed_priorities = None
    if priority:
        allowed_priorities = []
        for p in priority.split(","):
            p_strip = p.strip().upper()
            if p_strip not in Priority.__members__:
                raise typer.BadParameter(f"Invalid priority: '{p}'")
            allowed_priorities.append(Priority[p_strip])

    try:
        items = list_items(_resolve_target_path())
    except BacklogItemParseError as e:
        if format == "json":
            _print_json_error("PARSING_ERROR", str(e), {"filepath": str(e.filepath)})
        else:
            console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from e

    if category:
        items = [i for i in items if i.category == category]
    if allowed_priorities is not None:
        items = [i for i in items if i.priority in allowed_priorities]
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

    _output_items(items, format=format, show_score=(sort == "score"))


@app.command()
def show(
    item_id: str = typer.Argument(..., help="Item ID to show"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Show full details of a backlog item."""
    try:
        item = show_item(item_id, _resolve_target_path())
    except BacklogItemParseError as e:
        if json_output:
            _print_json_error("PARSING_ERROR", str(e), {"filepath": str(e.filepath)})
        else:
            console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from e
    except ValueError as e:
        if json_output:
            _print_json_error("INVALID_INPUT", str(e))
        else:
            console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from e

    if item is None:
        if json_output:
            _print_json_error("ITEM_NOT_FOUND", f"Item '{item_id}' not found.")
        else:
            console.print(f"[red]Item '{item_id}' not found.[/red]")
        raise typer.Exit(1)

    if json_output:
        _print_json_success(item.model_dump(mode="json"))
        return

    console.print(
        f"[bold cyan]{item.id}[/bold cyan]  [red]{item.priority.value}[/red]  "
        f"[yellow]{item.category.value}[/yellow]"
    )
    console.print(f"[bold white]{item.title}[/bold white]")
    console.print()
    console.print(f"Status: [bold]{item.effective_status.value}[/bold]")
    console.print(f"Effort: {item.effort.value}  |  Impact: {item.impact.value}  |  Score: {int(item.score)}")
    console.print(f"Project: {item.project}  |  Source: {item.source or '-'}")
    if item.tags:
        console.print(f"Tags: {', '.join(item.tags)}")
    if item.related_docs:
        console.print(f"Related docs: {', '.join(item.related_docs)}")
    if item.depends_on:
        all_items = {i.id: i for i in list_items(_resolve_target_path())}
        dep_statuses = []
        has_blocking = False
        for dep_id in item.depends_on:
            dep = all_items.get(dep_id)
            if dep is None:
                dep_statuses.append(f"[red]{dep_id} (missing)[/red]")
                has_blocking = True
            elif dep.effective_status == Status.DONE:
                dep_statuses.append(f"[green]{dep_id} ({dep.effective_status.value})[/green]")
            else:
                dep_statuses.append(f"[red]{dep_id} ({dep.effective_status.value})[/red]")
                has_blocking = True
        console.print(f"Depends on: {', '.join(dep_statuses)}")
        if item.effective_status == Status.BLOCKED and has_blocking:
            console.print("[yellow]Blocked: some dependencies are not done[/yellow]")
    console.print(f"Created: {item.created}  |  Updated: {item.updated}")
    if item.fixed_at:
        console.print(f"Fixed at: {item.fixed_at}")
    if item.body.strip():
        console.print()
        console.print(item.body)


@app.command()
def add(
    title: str = typer.Option(..., "--title", "-T", help="Item title"),
    category: Category = typer.Option(..., "--category", "-c", help="Category"),
    priority: Priority = typer.Option(..., "--priority", help="Priority (P0-P3)"),
    effort: Effort | None = typer.Option(None, "--effort", "-e", help="Effort estimate"),
    impact: Impact | None = typer.Option(None, "--impact", "-i", help="Impact level"),
    tags: str = typer.Option("", "--tags", help="Comma-separated tags"),
    source: str = typer.Option("", "--source", help="Source label"),
    depends_on: str = typer.Option("", "--depends-on", help="Comma-separated dependency IDs"),
    related_docs: str = typer.Option("", "--related-docs", help="Comma-separated related document references"),
    body: str | None = typer.Option(None, "--body", "-b", help="Description body (markdown)"),
    body_file: Path | None = typer.Option(None, "--body-file", help="Read body from file"),
    stdin: bool = typer.Option(False, "--stdin", help="Read body from stdin"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Add a new backlog item."""
    applied_defaults = []
    if effort is None:
        effort = Effort.M
        applied_defaults.append("effort (M)")
    if impact is None:
        impact = Impact.MEDIUM
        applied_defaults.append("impact (medium)")

    tag_list = _parse_csv_list(tags)
    dep_list = _parse_csv_list(depends_on)
    related_doc_list = _parse_csv_list(related_docs)
    body_content = _resolve_body(body, body_file, stdin) or ""
    project_name = _resolve_project_name()

    try:
        item = BacklogItem(
            id="AUTO",
            project=project_name,
            title=title,
            category=category,
            priority=priority,
            effort=effort,
            impact=impact,
            tags=tag_list,
            source=source,
            depends_on=dep_list,
            related_docs=related_doc_list,
            body=body_content,
        )
        filepath = add_item(item, _resolve_target_path())
    except BacklogItemParseError as e:
        if json_output:
            _print_json_error("PARSING_ERROR", str(e), {"filepath": str(e.filepath)})
        else:
            console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from e
    except FileExistsError as e:
        if json_output:
            _print_json_error("ITEM_CONFLICT", str(e))
        else:
            console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from e
    except ValueError as e:
        if json_output:
            _print_json_error("INVALID_INPUT", str(e))
        else:
            console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from e

    warnings_list = get_warnings()
    if applied_defaults:
        warnings_list.append(f"Defaults applied: {', '.join(applied_defaults)}. Please evaluate them if necessary.")

    if json_output:
        _print_json_success({
            "id": item.id,
            "filepath": str(filepath),
        }, warnings=warnings_list if warnings_list else None)
        return

    if warnings_list:
        for w in warnings_list:
            stderr_console.print(f"[yellow]Warning: {w}[/yellow]", style="yellow")
    console.print(f"[green]Created[/green] {item.id} → {filepath}")
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
    depends_on: str | None = typer.Option(None, "--depends-on", help="Comma-separated dependency IDs (replaces)"),
    related_docs: str | None = typer.Option(
        None, "--related-docs", help="Comma-separated related document references (replaces)"
    ),
    fixed: bool = typer.Option(False, "--fixed", "-f", help="Mark as done with today's date (shorthand)"),
    body: str | None = typer.Option(None, "--body", "-b", help="New body text"),
    body_file: Path | None = typer.Option(None, "--body-file", help="Read body from file"),
    stdin: bool = typer.Option(False, "--stdin", help="Read body from stdin"),
    expected_revision: str | None = typer.Option(
        None, "--expected-revision", help="Expected item revision for optimistic locking"
    ),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Update a backlog item."""
    if fixed and status is not None and status != Status.DONE:
        raise typer.BadParameter("Cannot set both --fixed and a non-done --status.")
    updates: dict = {}
    body_content = _resolve_body(body, body_file, stdin)

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
        updates["tags"] = _parse_csv_list(tags)
    if source is not None:
        updates["source"] = source
    if depends_on is not None:
        updates["depends_on"] = _parse_csv_list(depends_on)
    if related_docs is not None:
        updates["related_docs"] = _parse_csv_list(related_docs)
    if body_content is not None:
        updates["body"] = body_content
    if fixed:
        updates["status"] = Status.DONE
        updates["fixed_at"] = date.today()
        updates.pop("fixed", None)

    if not updates:
        if json_output:
            _print_json_error("INVALID_INPUT", "No updates specified.")
        else:
            console.print("[yellow]No updates specified.[/yellow]")
        raise typer.Exit(1)

    try:
        result = update_item(item_id, updates, _resolve_target_path(), expected_revision=expected_revision)
    except BacklogItemParseError as e:
        if json_output:
            _print_json_error("PARSING_ERROR", str(e), {"filepath": str(e.filepath)})
        else:
            console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from e
    except ValueError as e:
        if json_output:
            _print_json_error("INVALID_INPUT", str(e))
        else:
            console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from e

    if result is None:
        if json_output:
            _print_json_error("ITEM_NOT_FOUND", f"Item '{item_id}' not found.")
        else:
            console.print(f"[red]Item '{item_id}' not found.[/red]")
        raise typer.Exit(1)

    warnings_list = get_warnings()
    if json_output:
        _print_json_success(result.model_dump(mode="json"), warnings=warnings_list if warnings_list else None)
        return

    if warnings_list:
        for w in warnings_list:
            stderr_console.print(f"[yellow]Warning: {w}[/yellow]", style="yellow")
    console.print(f"[green]Updated[/green] {item_id} — {result.title}")


@app.command()
def stats(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Show backlog statistics."""
    try:
        items = list_items(_resolve_target_path())
    except BacklogItemParseError as e:
        if json_output:
            _print_json_error("PARSING_ERROR", str(e), {"filepath": str(e.filepath)})
        else:
            console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from e

    if not items:
        if json_output:
            _print_json_success({
                "total": 0, "active": 0, "done": 0,
                "by_status": {}, "by_priority": {}, "by_category": {},
            })
        else:
            console.print("[dim]No items.[/dim]")
        return

    by_status: dict[str, int] = {}
    by_category: dict[str, int] = {}
    by_priority: dict[str, int] = {}
    for item in items:
        by_status[item.effective_status.value] = by_status.get(item.effective_status.value, 0) + 1
        by_category[item.category.value] = by_category.get(item.category.value, 0) + 1
        by_priority[item.priority.value] = by_priority.get(item.priority.value, 0) + 1

    total = len(items)
    active = sum(1 for i in items if i.effective_status in (Status.TODO, Status.IN_PROGRESS, Status.BLOCKED))
    done = sum(1 for i in items if i.effective_status == Status.DONE)

    if json_output:
        _print_json_success({
            "total": total,
            "active": active,
            "done": done,
            "by_status": by_status,
            "by_priority": by_priority,
            "by_category": by_category,
        })
        return

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
    limit: int = typer.Option(5, "--limit", "-n", help="Number of items to show"),
    status: Status = typer.Option(
        Status.TODO, "--status", help="Filter by status (todo/in_progress)"
    ),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Show recommended next items sorted by priority score."""
    if status not in (Status.TODO, Status.IN_PROGRESS):
        raise typer.BadParameter("Only 'todo' or 'in_progress' status can be recommended.")
    if limit < 0:
        raise typer.BadParameter("Limit must be a non-negative integer.")

    try:
        items = list_items(_resolve_target_path())
    except BacklogItemParseError as e:
        if json_output:
            _print_json_error("PARSING_ERROR", str(e), {"filepath": str(e.filepath)})
        else:
            console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from e

    active = [i for i in items if i.effective_status == status and i.score > 0]
    active.sort(key=lambda x: x.score, reverse=True)
    active = active[:limit]

    warnings_list = get_warnings()

    if not active:
        if json_output:
            _print_json_success([], warnings=warnings_list if warnings_list else None)
        else:
            if warnings_list:
                for w in warnings_list:
                    stderr_console.print(f"[yellow]Warning: {w}[/yellow]", style="yellow")
            console.print(f"[dim]No active {status.value} items to recommend.[/dim]")
        return

    if json_output:
        _print_json_success(
            [i.model_dump(mode="json") for i in active],
            warnings=warnings_list if warnings_list else None
        )
    else:
        if warnings_list:
            for w in warnings_list:
                stderr_console.print(f"[yellow]Warning: {w}[/yellow]", style="yellow")
        _print_table(active, show_score=True)


@app.command()
def index(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Generate docs/backlog/INDEX.md overview."""
    try:
        content = generate_index(_resolve_target_path())
        backlog_dir = get_backlog_dir(_resolve_target_path(), create=True)
        index_path = backlog_dir / INDEX_FILENAME
        index_path.write_text(content)
    except BacklogItemParseError as e:
        if json_output:
            _print_json_error("PARSING_ERROR", str(e), {"filepath": str(e.filepath)})
        else:
            console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from e
    except ValueError as e:
        if json_output:
            _print_json_error("INVALID_INPUT", str(e))
        else:
            console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from e

    if json_output:
        _print_json_success({"filepath": str(index_path)})
        return
    console.print(f"[green]Generated[/green] {index_path}")


@app.command()
def edit(
    item_id: str = typer.Argument(..., help="Item ID to edit"),
    stdin: bool = typer.Option(False, "--stdin", help="Replace body from stdin"),
):
    """Open a backlog item in $EDITOR, or replace body via --stdin."""
    try:
        filepath = get_item_filepath(item_id, _resolve_target_path(), create=False)
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from e
    if not filepath.exists():
        console.print(f"[red]Item '{item_id}' not found.[/red]")
        raise typer.Exit(1)

    if stdin:
        new_body = sys.stdin.read()
        try:
            result = update_item(item_id, {"body": new_body}, _resolve_target_path())
        except BacklogItemParseError as e:
            console.print(f"[red]Error: {e}[/red]")
            raise typer.Exit(1) from e
        except ValueError as e:
            console.print(f"[red]Error: {e}[/red]")
            raise typer.Exit(1) from e
        if result is None:
            console.print(f"[red]Item '{item_id}' not found.[/red]")
            raise typer.Exit(1)
        console.print(f"[green]Updated[/green] {item_id} body from stdin")
        return

    if not sys.stdout.isatty():
        console.print("[red]stdout is not a TTY: cannot open editor.[/red]")
        console.print("[dim]Use --stdin to pipe content, or use update --body/--body-file/--stdin[/dim]")
        raise typer.Exit(1)

    editor = os.environ.get("EDITOR", "vim")
    os.system(f"{editor} {filepath}")


def run_cli():
    is_json = "--json" in sys.argv or any(
        sys.argv[i] == "--format" and i + 1 < len(sys.argv) and sys.argv[i + 1] == "json"
        for i in range(len(sys.argv))
    )
    if is_json:
        try:
            click_app = typer.main.get_command(app)
            ret_code = click_app.main(standalone_mode=False)
            if isinstance(ret_code, int) and ret_code != 0:
                sys.exit(ret_code)
        except click.exceptions.ClickException as e:
            _print_json_error("INVALID_INPUT", e.format_message())
            sys.exit(e.exit_code)
        except click.exceptions.Abort:
            _print_json_error("ABORTED", "Operation aborted.")
            sys.exit(1)
        except (typer.Exit, click.exceptions.Exit) as e:
            sys.exit(e.exit_code)
        except Exception as e:
            code = "INTERNAL_ERROR"
            msg = str(e)
            if isinstance(e, FileExistsError):
                code = "ITEM_CONFLICT"
            elif isinstance(e, ValueError):
                code = "REVISION_MISMATCH" if "Revision mismatch" in msg else "INVALID_INPUT"
            elif "not found" in msg.lower():
                code = "ITEM_NOT_FOUND"
            elif "BacklogItemParseError" in type(e).__name__:
                code = "PARSING_ERROR"
            _print_json_error(code, msg)
            sys.exit(1)
    else:
        app()


if __name__ == "__main__":
    run_cli()
