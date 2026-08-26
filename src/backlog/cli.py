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
    BacklogItemParseError,
    PatchResult,
    RevisionConflictError,
    add_item,
    generate_index,
    get_warnings,
    list_items,
    patch_item,
    show_item,
    update_item,
    validate_store_items,
)
from .models import (
    BacklogItem,
    Category,
    Effort,
    Impact,
    Priority,
    Status,
)
from .store import StoreContext, StoreLoadError, load_store

app = typer.Typer(help="Unified backlog manager")
console = Console()
stderr_console = Console(stderr=True)
_store_context: StoreContext | None = None


class _CliInputError(click.ClickException):
    """A stable CLI input error that can be rendered as JSON by run_cli."""

    error_code = "INVALID_INPUT"


class _CliStoreError(_CliInputError):
    """A stable CLI store-validation error."""

    error_code = "STORE_INVALID"


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
    store: Annotated[
        Path,
        typer.Option("--store", help="Exact absolute portable Backlog Store root"),
    ],
):
    global _store_context
    try:
        _store_context = load_store(store)
    except StoreLoadError as error:
        raise _CliStoreError(str(error)) from error


def _resolve_store_context() -> StoreContext:
    """Return the exact StoreContext loaded by the CLI callback."""
    if _store_context is None:
        raise _CliStoreError("--store is required.")
    return _store_context


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


def _output_items(
    items: list[BacklogItem],
    format: str,
    show_score: bool = False,
    all_items: list[BacklogItem] | None = None,
) -> None:
    """Unified output: JSON, CSV, or Rich table."""
    if format == "json":
        _print_json_success(_agent_items(items, all_items=all_items))
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


def _blocked_by(item: BacklogItem, item_by_id: dict[str, BacklogItem]) -> list[str]:
    """Return unresolved dependencies for an item without persisting derived state."""
    return [
        dependency
        for dependency in item.depends_on
        if (candidate := item_by_id.get(dependency)) is None or candidate.status != Status.DONE
    ]


def _agent_item(item: BacklogItem, item_by_id: dict[str, BacklogItem]) -> dict:
    """Render one stable Agent JSON item record, including derived dependency state."""
    data = item.model_dump(mode="json")
    data["blocked_by"] = _blocked_by(item, item_by_id)
    return data


def _agent_items(items: list[BacklogItem], *, all_items: list[BacklogItem] | None = None) -> list[dict]:
    item_by_id = {item.id: item for item in all_items or items}
    return [_agent_item(item, item_by_id) for item in items]


def _agent_mutation(
    outcome: PatchResult,
    store: StoreContext,
    *,
    filepath: Path | None = None,
) -> dict:
    """Render a mutation receipt while retaining flattened item fields for compatibility."""
    item_by_id = {item.id: item for item in list_items(store)}
    before = _agent_item(outcome.before, item_by_id)
    result = _agent_item(outcome.result, item_by_id)
    receipt = {
        "store": {
            "project_id": store.manifest.project_id,
            "id_prefix": store.manifest.id_prefix,
        },
        "before": before,
        "result": result,
        "changed_fields": outcome.changed_fields,
        "revision": outcome.result.revision,
        "no_op": outcome.no_op,
        **result,
    }
    if filepath is not None:
        receipt["filepath"] = str(filepath)
    return receipt


@app.command(name="list")
def list_cmd(
    category: Category | None = typer.Option(None, "--category", "-c", help="Filter by category"),
    priority: str | None = typer.Option(None, "--priority", help="Filter by priority (comma-separated, e.g. P0,P1)"),
    status: Status | None = typer.Option(None, "--status", "-s", help="Filter by status"),
    tag: str | None = typer.Option(None, "--tag", "-t", help="Filter by tag"),
    sort: str = typer.Option("score", "--sort", help="Sort by: score, priority, id"),
    limit: int = typer.Option(0, "--limit", "-n", help="Limit results"),
    format: str = typer.Option("table", "--format", help="Output format: json, table, csv"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
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
        all_items = list_items(_resolve_store_context())
    except BacklogItemParseError as e:
        if format == "json":
            _print_json_error("PARSING_ERROR", str(e), {"filepath": str(e.filepath)})
        else:
            console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from e

    items = list(all_items)
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

    _output_items(items, format=format, show_score=(sort == "score"), all_items=all_items)


@app.command()
def show(
    item_id: str = typer.Argument(..., help="Item ID to show"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Show full details of a backlog item."""
    try:
        store = _resolve_store_context()
        item = show_item(item_id, store)
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
        _print_json_success(_agent_item(item, {candidate.id: candidate for candidate in list_items(store)}))
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
        all_items = {i.id: i for i in list_items(store)}
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
    store = _resolve_store_context()

    try:
        item = BacklogItem(
            id="AUTO",
            project=store.manifest.project_id,
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
        filepath = add_item(item, store)
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
        created = PatchResult(
            before=item.model_copy(deep=True),
            result=item,
            changed_fields=sorted(
                field
                for field in item.model_dump(mode="json", exclude={"score", "effective_status", "extra"})
                if field not in {"is_blocked"}
            ),
            no_op=False,
        )
        _print_json_success(
            _agent_mutation(created, store, filepath=filepath) | {"before": None},
            warnings=warnings_list if warnings_list else None,
        )
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
        store = _resolve_store_context()
        result = patch_item(item_id, updates, store, expected_revision=expected_revision)
    except BacklogItemParseError as e:
        if json_output:
            _print_json_error("PARSING_ERROR", str(e), {"filepath": str(e.filepath)})
        else:
            console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from e
    except RevisionConflictError as e:
        if json_output:
            _print_json_error("REVISION_MISMATCH", str(e))
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
        _print_json_success(
            _agent_mutation(result, store),
            warnings=warnings_list if warnings_list else None,
        )
        return

    if warnings_list:
        for w in warnings_list:
            stderr_console.print(f"[yellow]Warning: {w}[/yellow]", style="yellow")
    console.print(f"[green]Updated[/green] {item_id} — {result.result.title}")


@app.command()
def stats(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Show backlog statistics."""
    try:
        items = list_items(_resolve_store_context())
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
    status: Status | None = typer.Option(
        None, "--status", help="Filter by effective status"
    ),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Show the human recommendation view or the Agent work queue."""
    queue_statuses = (Status.TODO, Status.IN_PROGRESS, Status.BLOCKED)
    if json_output and status is not None and status not in queue_statuses:
        raise typer.BadParameter("Agent work queues only support todo, in_progress, or blocked status filters.")
    if not json_output and status is not None and status not in (Status.TODO, Status.IN_PROGRESS):
        raise typer.BadParameter("Only 'todo' or 'in_progress' status can be recommended.")
    if limit < 0:
        raise typer.BadParameter("Limit must be a non-negative integer.")

    try:
        items = list_items(_resolve_store_context())
    except BacklogItemParseError as e:
        if json_output:
            _print_json_error("PARSING_ERROR", str(e), {"filepath": str(e.filepath)})
        else:
            console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from e

    warnings_list = get_warnings()

    if json_output:
        queue_states = {
            Status.IN_PROGRESS: "in_progress",
            Status.TODO: "ready",
            Status.BLOCKED: "blocked",
        }
        queue = [
            item
            for item in items
            if item.effective_status in queue_states and (status is None or item.effective_status == status)
        ]
        queue.sort(
            key=lambda item: (
                {"in_progress": 0, "ready": 1, "blocked": 2}[queue_states[item.effective_status]],
                item.priority.value,
                item.id,
            )
        )
        queue = queue[:limit]
        item_by_id = {item.id: item for item in items}
        rendered_queue = []
        for item in queue:
            record = _agent_item(item, item_by_id)
            record["queue_state"] = queue_states[item.effective_status]
            score = record.pop("score")
            record["score_hint"] = {
                "value": score,
                "formula": "priority_weight * impact_weight * effort_weight",
            }
            rendered_queue.append(record)
        _print_json_success(
            rendered_queue,
            warnings=warnings_list if warnings_list else None,
        )
        return

    human_status = status or Status.TODO
    active = [i for i in items if i.effective_status == human_status and i.score > 0]
    active.sort(key=lambda x: x.score, reverse=True)
    active = active[:limit]

    if not active:
        if warnings_list:
            for w in warnings_list:
                stderr_console.print(f"[yellow]Warning: {w}[/yellow]", style="yellow")
        console.print(f"[dim]No active {human_status.value} items to recommend.[/dim]")
        return

    if warnings_list:
        for w in warnings_list:
            stderr_console.print(f"[yellow]Warning: {w}[/yellow]", style="yellow")
    _print_table(active, show_score=True)


@app.command()
def index(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Generate the exact Store INDEX.md overview."""
    try:
        store = _resolve_store_context()
        content = generate_index(store)
        index_path = store.index_path
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
        store = _resolve_store_context()
        current = show_item(item_id, store)
        if current is None:
            raise ValueError(f"Item '{item_id}' not found.")
        filepath = store.items_path / f"{item_id}.md"
    except (StoreLoadError, ValueError) as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from e
    if not filepath.exists():
        console.print(f"[red]Item '{item_id}' not found.[/red]")
        raise typer.Exit(1)

    if stdin:
        new_body = sys.stdin.read()
        try:
            result = update_item(item_id, {"body": new_body}, store)
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


@app.command(name="validate-store")
def validate_store(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Validate all Store@1 items without creating or modifying store entries."""
    try:
        store = _resolve_store_context()
        items = validate_store_items(store)
    except (BacklogItemParseError, StoreLoadError, ValueError) as error:
        if json_output:
            _print_json_error("STORE_INVALID", str(error))
        else:
            console.print(f"[red]Error: {error}[/red]")
        raise typer.Exit(1) from error
    data = {
        "store": str(store.root),
        "project_id": store.manifest.project_id,
        "id_prefix": store.manifest.id_prefix,
        "items": len(items),
    }
    if json_output:
        _print_json_success(data)
        return
    console.print(f"[green]Validated[/green] {store.root} ({len(items)} items)")


def run_cli():
    is_json = "--json" in sys.argv or any(
        argument == "--format=json"
        or (argument == "--format" and index + 1 < len(sys.argv) and sys.argv[index + 1] == "json")
        for index, argument in enumerate(sys.argv)
    )
    if is_json:
        try:
            click_app = typer.main.get_command(app)
            ret_code = click_app.main(standalone_mode=False)
            if isinstance(ret_code, int) and ret_code != 0:
                sys.exit(ret_code)
        except click.exceptions.ClickException as e:
            code = e.error_code if isinstance(e, _CliInputError) else "INVALID_INPUT"
            _print_json_error(code, e.format_message())
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
