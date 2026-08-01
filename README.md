# Backlog CLI

Backlog CLI is a lightweight file-based task tracker for projects. It stores each item as a Markdown file under
`backlog/items/` for Project Ops targets or `docs/backlog/items/` for repository targets, with YAML frontmatter
for structured fields and Markdown body text for context.

The tool is designed for both human developers and AI agents:

- humans get readable Markdown files and git-friendly diffs
- agents get stable commands, JSON output, and a documented operation contract
- backlog data can live in a Project Ops directory or inside a repository, without a database

## Install

This project uses `uv`.

```bash
uv sync
uv run backlog --help
```

In a checked-out development environment, the local virtualenv command also works:

```bash
./.venv/bin/backlog --help
```

## Basic Usage

The authoritative interface accepts an exact, absolute portable store root. It must already contain a valid
`backlog.json`, `items/`, and `INDEX.md`; read commands do not create files or discover parent/child directories.

```bash
uv run backlog --store /absolute/path/to/backlog list --status todo
uv run backlog --store /absolute/path/to/backlog next -n 5 --json
uv run backlog --store /absolute/path/to/backlog show PRO-001
uv run backlog --store /absolute/path/to/backlog update PRO-001 --fixed
uv run backlog --store /absolute/path/to/backlog stats
```

Create a new item:

```bash
uv run backlog --store /absolute/path/to/backlog add \
  -T "Add login audit logging" \
  -c feature \
  --priority P1 \
  -e S \
  -i high \
  -b "Record successful and failed login attempts; verify with unit tests."
```

For multiline Markdown bodies, prefer `--body-file` or `--stdin`:

```bash
uv run backlog --store /absolute/path/to/backlog add \
  -T "Document deployment rollback" \
  -c docs \
  --priority P2 \
  --body-file /tmp/body.md
```

`--target` and CWD discovery remain compatibility adapters for existing `backlog/` and `docs/backlog/` layouts.
They cannot be combined with `--store`. To adopt an existing legacy store, first ensure it has regular `items/` and
`INDEX.md` entries, then provide its identity explicitly; the command rejects existing manifests and item identity
conflicts without overwriting them:

```bash
uv run backlog --target /path/to/legacy-project provision-store \
  --project-id my-project --id-prefix PRO
```

## Data Model

Each item has fields such as:

- `id`: exact stores generate it from manifest `id_prefix`, for example `BAC-001`; legacy derivation is compatibility-only
- `project`: exact stores take this identifier from manifest `project_id`
- `title`: short task title
- `category`: `bug`, `feature`, `docs`, `testing`, `ops`, and other supported categories
- `priority`: `P0`, `P1`, `P2`, `P3`
- `effort`: `XS`, `S`, `M`, `L`, `XL`
- `impact`: `high`, `medium`, `low`
- `status`: `todo`, `in_progress`, `blocked`, `done`, `cancelled`
- `fixed_at`: completion date, valid only when `status=done`

`next` and `list --sort score` use a computed score:

```text
score = priority_weight * impact_weight * effort_weight
```

`next` recommends only `todo` and `in_progress` items. JSON output includes `score` so callers can explain ordering.

## AI Agent Contract

AI agents should use the repo-owned skill at [skills/backlog/SKILL.md](skills/backlog/SKILL.md) as the command
and behavior contract. That document is the source of truth for agent-safe invocation patterns, JSON parsing,
status transitions, and cross-project usage.

[agent/AGENT_CONTRACT.md](agent/AGENT_CONTRACT.md) remains as a compatibility redirect for older links.

Project architecture is documented in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Development

```bash
uv run pytest
uv run ruff check .
uv run pyright
```

The repository keeps dependencies intentionally small: Typer, Pydantic, python-frontmatter, Rich, and PyYAML.
