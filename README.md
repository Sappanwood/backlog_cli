# Backlog CLI

Backlog CLI is a Git-native, Markdown-first portable task-intent ledger. A `backlog/Store@1` stores each item as
Markdown with YAML frontmatter and a Markdown body for its intent and acceptance boundary. It can live in Project
Ops, an independent repository, or another host without a database.

The tool is designed for both human developers and AI agents:

- humans get readable Markdown files and git-friendly diffs
- agents get stable commands, JSON output, and a documented operation contract
- hosts can supply the same portable store without coupling the core to their own routing model

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

## Portable Store and Basic Usage

The exact core accepts an exact, absolute portable store root. `backlog.json` is store identity's sole authority:
it declares `schema: "backlog/Store@1"`, `project_id`, and `id_prefix`. The root must already contain that manifest,
`items/`, and `INDEX.md`; read commands do not create files or discover parent/child directories. Mutations use an
advisory lock on the canonical store directory inode and do not create a `.lock` file in the store.

```bash
uv run backlog --store /absolute/path/to/backlog list --status todo
uv run backlog --store /absolute/path/to/backlog next -n 5 --json
uv run backlog --store /absolute/path/to/backlog show PRO-001
uv run backlog --store /absolute/path/to/backlog update PRO-001 --status done --json
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
They cannot be combined with `--store`; each adapter resolves one store before entering the same exact core. To adopt
an existing legacy store, first ensure it has regular `items/` and
`INDEX.md` entries, then provide its identity explicitly; the command rejects existing manifests and item identity
conflicts without overwriting them:

```bash
uv run backlog --target /path/to/legacy-project provision-store \
  --project-id my-project --id-prefix PRO
```

Administrators can validate every persisted item without writing before provisioning:

```bash
uv run backlog --target /path/to/legacy-project validate-store \
  --project-id my-project --id-prefix PRO --json
uv run backlog --store /absolute/path/to/backlog validate-store --json
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

The human `next` view retains score-based recommendations for compatibility. Agent JSON instead returns a work queue
whose records identify `queue_state` as `in_progress`, `ready`, or `blocked`, include `blocked_by`, and expose score
only as the explainable `score_hint` compatibility field. Without `--status`, Agent JSON returns all three states;
an explicit `--status todo|in_progress|blocked` filters by effective status.

## AI Agent Contract

AI agents should use the repo-owned skill at [skills/backlog/SKILL.md](skills/backlog/SKILL.md) as the command
and behavior contract. That document is the source of truth for agent-safe invocation patterns, JSON parsing,
status transitions, and cross-project usage.

The host first resolves the exact store. In the Workspace Control environment, its Catalog resolver owns project or
workspace routing and passes the resulting `backlog/store@1` root to `backlog --store`; backlog-cli never reads that
Catalog. The five official Agent operations are `list`, `show`, `add`, `update`, and `next`, each invoked with
`--json`.
Every successful call returns `{"ok": true, "data": ...}` and every failure returns
`{"ok": false, "error": {"code", "message", "details"}}`; successful calls exit 0 and operation failures exit 1
(CLI usage errors exit 2). `add` and `update` return a mutation receipt with `before`, `result`,
`changed_fields`, `revision`, and `no_op`. `update --expected-revision` rejects stale writes with
`REVISION_MISMATCH`; setting `--status done` fills `fixed_at` automatically.

[agent/AGENT_CONTRACT.md](agent/AGENT_CONTRACT.md) remains as a compatibility redirect for older links.

Project architecture is documented in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Responsibility Boundaries

Backlog CLI owns portable task intent, item CRUD, declared and derived status, dependencies, revisions, and the
derived index. Compatibility adapters preserve older layouts but are not a new integration surface.

Workspace Control owns local project routing, Catalog descriptors, and development services. Sigil owns execution
workflow concerns such as runs, claims, checkpoints, submissions, review, worktrees, scheduling, and deciding whether
work is complete. Neither host responsibility is persisted in `backlog/Store@1`.

## Development

```bash
uv run pytest
uv run ruff check .
uv run pyright
```

The repository keeps dependencies intentionally small: Typer, Pydantic, python-frontmatter, Rich, and PyYAML.
