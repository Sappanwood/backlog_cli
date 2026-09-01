# ARCHITECTURE.md — Backlog CLI 架构文档

## 架构概览

图意：箭头表示调用与数据流，而不是 Python 模块 import 或依赖关系。CLI 严格载入 exact Store，
再将 `StoreContext` 作为输入传给 `items.py` core：

```mermaid
flowchart TD
    CLI[cli.py - Typer CLI] --> Exact[exact `--store` entry]
    Exact --> StrictLoad[strict `load_store()`]
    StrictLoad --> Context[StoreContext]
    Context --> Items[items.py - exact core CRUD & I/O]
    Items <-->|items and INDEX| Files[(exact backlog/items/*.md)]
    Items --> Result[JSON data or human render data]
    Result --> CLI
```

`store.py` 实现 strict `load_store()` 与 `StoreContext`。CLI 只接受 `--store`，并将严格载入的
`StoreContext` 交给 `Items` core；core 不读取 Catalog，也不执行目录 discovery。

## 核心技术栈

| 层 | 组件 | 选型 | 理由 |
|----|------|------|------|
| CLI | Typer | 0.15+ | Click-based，Python 3.12+ type hint 友好 |
| 输出 | Rich | 13.0+ | 终端表格/颜色，开发体验好 |
| 模型 | Pydantic | 2.10+ | 数据验证 + 序列化，枚举类型安全 |
| 序列化 | python-frontmatter | 1.1+ | YAML frontmatter + Markdown body 读写 |
| 构建 | hatchling | PEP 621 | 轻量，零配置 |

## Portable Backlog Store

`backlog/Store@1` 以一个准确的绝对 store root 为 authority，不依赖 Workspace Control、Project Ops、
固定用户路径或目录名推导。其静态结构为：

```text
backlog/
├── backlog.json
├── items/
└── INDEX.md
```

`backlog.json` 是 store identity 的唯一 authority，是严格 schema，且只能包含以下字段：

```json
{
  "schema": "backlog/Store@1",
  "project_id": "backlog-cli",
  "id_prefix": "BAC"
}
```

- `schema` 必须精确为 `backlog/Store@1`。
- `project_id` 必须以字母或数字开始，之后只能使用字母、数字、`-`、`_`。
- `id_prefix` 必须以大写字母开始，之后只能使用大写字母、数字、`_`。
- `backlog.json`、`items/` 和 `INDEX.md` 均为必需的直接 entry，分别必须是普通文件、目录和普通文件；
  其他 root entry 不属于 Store schema，loader 不扫描或解释它们。loader 和 read path 不创建任何 entry。

`store.load_store(root)` 返回不可变的 `StoreContext`，统一持有 canonical root、manifest、manifest path、
items path、index path 和 canonical directory lock path。它仅接受绝对 root，并拒绝缺失、非规则 entry、静态 symlink 或
解析后逃逸 root 的路径。当前安全边界是受信任本机用户和受信任 store root：防御静态 containment escape
并支持正常并发；不承诺抵抗同一用户在校验后替换 ancestor 的攻击。

## 核心流程

### 数据存储

```
<exact-store>/
├── backlog.json
├── INDEX.md              # generate_index() 自动生成
└── items/
    ├── ZHI-001.md         # 每项一个文件
    ├── ZHI-002.md
    └── INK-001.md
```

每文件格式：

```
---
id: "ZHI-001"
project: "zhijian"
title: "..."
category: feature
priority: P1
status: todo
...
---

Markdown 正文（body）
```

### ID 生成流程

1. Core 只从 `StoreContext.manifest.id_prefix` 取得前缀。
2. 在 exact canonical store root 的目录 inode 上取得 POSIX `flock` 排他锁后分配 ID；使用
   `O_DIRECTORY|O_CLOEXEC` 打开目录，不在 store 内创建 `.lock` 或其他 runtime entry。
3. 扫描该前缀在 exact `items/` 目录中的最大序号，返回 `<前缀>-<最大序号+1>`（三位补零，如 `BAC-023`）。
4. 创建和 patch 均验证 item 的 `project` 和 ID prefix 与 manifest 一致；验证失败、依赖失败和 revision conflict 不会写入 item 或 index。
5. mutation 取得 store lock 后重新载入同一 exact root 的 manifest；identity 改变时会在任何写入前 fail closed。

### CLI Store 入口

`backlog --store <absolute-exact-root> <command>` 是权威 CLI entrypoint。exact core 的 CLI callback 通过
`load_store()` 严格载入 manifest；它不会从 parent、child、Repo、Project Ops 或 CWD 推导 store。
invalid store 在 JSON 调用中返回稳定的 `STORE_INVALID` error envelope 和非零退出码。

`items.py` 的 `add_item`、`update_item`、`next_id`、`generate_index` 与 `check_dependencies` 只接受
`StoreContext`，不执行路径推导。`patch_item` 在同一锁内比较
expected revision、验证依赖、识别 no-op，并仅在有实际变更时生成新 revision、写入 item 和重建 index；
`update_item` 只返回更新后的条目；CLI 的 JSON receipt 使用 `patch_item`。

### Agent JSON 与 work queue

`list` 与 `show` 的 JSON 条目同时保留声明 `status` 和派生 `effective_status`，并以 `blocked_by` 返回未完成或
缺失的依赖。`add` 与 `update` 的 JSON data 是 mutation receipt：`before`、`result`、`changed_fields`、
`revision`、`no_op` 和 store identity 均在一次调用中返回。revision conflict 使用稳定的
`REVISION_MISMATCH` error code；其他可预期输入错误使用 `INVALID_INPUT`，找不到条目使用
`ITEM_NOT_FOUND`。

`next --json` 是 Agent work queue：每条记录有 `queue_state`（`in_progress`、`ready` 或 `blocked`）与
`blocked_by`，队列按状态、priority、ID 确定性排序。队列只暴露 `score_hint` 作为兼容解释信息，不能当作
任务完成度或编排决定。未指定 `--status` 时返回全部三态；显式 `--status todo|in_progress|blocked` 按
effective status 过滤。`item_type=epic` 的协调容器不进入 human 或 Agent `next` 队列。非 JSON 的 `next`
和其他 human/admin commands 保持原有兼容表面。

### Epic ownership

Item hierarchy is intentionally limited to one level. `item_type` is `task` by default or `epic` for a coordination
container. A task may set `parent_id` to an existing epic in the same exact store; epics cannot have parents, and a
task cannot be used as a parent. Children are derived from `parent_id` and are never persisted as a second list.

Ownership is orthogonal to execution dependencies. `parent_id` never changes `effective_status` or `blocked_by`,
while `depends_on` continues to be the only relation that affects readiness. Epic status is declared by the caller;
the core does not infer completion from child status. Mutations and `validate-store` reject missing parents,
non-epic parents, self-parenting, and attempts to demote an epic that still owns children.

### 推荐排序

```
score = priority_weight × impact_weight × effort_weight
```

done/cancelled 条目 score=0。非 JSON 的 `next` 只推荐 todo/in_progress；`list --sort score`
仍可显示 blocked 条目的 score。Agent JSON work queue 不以 score 排序。

## 模块接口

### models.py → items.py, cli.py

```python
# 枚举
class Priority(str, Enum): P0, P1, P2, P3
class Status(str, Enum): todo, in_progress, done, cancelled, blocked
class Effort(str, Enum): XS, S, M, L, XL
class Impact(str, Enum): high, medium, low
class Category(str, Enum): bug, a11y, ux, ..., ops
class ItemType(str, Enum): task, epic

# 模型
class BacklogItem(BaseModel):
    id: str
    project: str
    title: str
    item_type: ItemType
    parent_id: str | None
    category: Category
    priority: Priority
    effort: Effort
    impact: Impact
    status: Status
    related_docs: list[str]
    revision: str  # 乐观锁版本指纹 (UUID hex 前8位)
    ...
    score: float  # @computed_field
```

### items.py → cli.py

```python
list_items(store: StoreContext) -> list[BacklogItem]
show_item(item_id: str, store: StoreContext) -> BacklogItem | None

add_item(item: BacklogItem, store: StoreContext) -> Path
update_item(item_id: str, updates: dict, store: StoreContext, expected_revision: str | None) -> BacklogItem | None
patch_item(item_id: str, updates: dict, store: StoreContext, expected_revision: str | None) -> PatchResult | None
next_id(store: StoreContext) -> str
generate_index(store: StoreContext) -> str
check_dependencies(item_id: str, depends_on: list[str], store: StoreContext) -> None
validate_parent_relations(items: list[BacklogItem]) -> None
```

`list_items` 和 `show_item` 是 portable core read API：调用者必须先通过
`store.load_store()` 获得准确的 `StoreContext`；它们不会检查 CWD、项目路径，也不会创建任何文件。

## 宿主与工作流责任边界

backlog-cli 的 exact core 只接收 StoreContext，负责 item CRUD、依赖计算、revision、原子索引和 JSON
envelope。`backlog.json` 是 store identity 的唯一 authority；core 不读取项目名、Workspace Catalog、
Project Ops 路径或 workflow 状态。

Workspace Control 是本机宿主：它从 Catalog resolved context 找到 `backlog/store@1` exact root，并以
`backlog --store` 调用本工具；它负责项目路由、typed descriptor 与开发服务，不能复制 backlog CRUD。
Sigil 是执行工作流：它负责 run、claim、checkpoint、submission、review、worktree、调度和完成判断。
这些运行时状态不写入 `backlog/Store@1`；backlog item 只保留跨会话的任务意图和验收边界。

人类 Rich/CSV、`stats`、`index`、`edit` 和 `--fixed` 由同一 exact store 入口提供。

## 关键设计决策

| 决策 | 选 A | 弃 B | 理由 |
|------|------|------|------|
| 存储方式 | 每个条目一个 .md 文件 | 单 JSON/YAML 文件 | 人类可读、git diff 友好、并发写入安全 |
| ID 方案 | 项目前3字母+序号 | UUID | 简短、人类可读、便于对话引用 |
| 全局 `--store` | 单回调设置一次 entrypoint context | 每个子命令重复解析 | 简化子命令签名，单次调用只操作一个 store |
