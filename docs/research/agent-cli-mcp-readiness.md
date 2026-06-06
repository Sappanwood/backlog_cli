---
date: 2026-06-07
scope: "审计 Backlog CLI 供 AI Agent 使用时的反直觉行为、误解风险与 MCP 前置优化"
findings_summary: "当前 CLI 可供人类交互使用，但尚不能作为可靠的 Agent API；在增加 MCP 层前必须先修复路径安全、无提示覆盖、状态派生污染、静默数据丢失和结构化协议不一致。"
informed_by:
  - AGENTS.md
  - agent/AGENT_CONTRACT.md
  - docs/ARCHITECTURE.md
  - src/backlog/cli.py
  - src/backlog/items.py
  - src/backlog/models.py
  - tests/test_cli.py
  - tests/test_items.py
  - tests/test_models.py
  - ~/.agents/skills/backlog/SKILL.md
informs: []
zhijian_entries: []
---

## 调研对象

本次调研聚焦 Backlog CLI 作为 AI Agent 操作接口时的可靠性，不评价 Rich 表格等人类交互体验。
调查方式包括：

- 阅读项目入口、Agent contract、全局 backlog skill、架构文档、源码和测试。
- 在隔离临时目录中实际调用 CLI，复现边界行为。
- 运行现有质量门禁：`uv run pytest && uv run ruff check . && uv run pyright`。

当前工作树已有未提交改动；本报告以这些改动后的当前实现为准，不修改或回退它们。

## 发现与证据

### 结论概览

当前 CLI 的主要问题不是“缺少 MCP 包装”，而是核心业务层尚未形成 Agent 可依赖的严格契约。
若直接把现有命令包成 MCP tools，MCP 只会把现有歧义变成结构化入口，无法消除数据损坏和误判风险。

| 等级 | 问题 | Agent 风险 |
|---|---|---|
| P0 | `project` 可形成路径逃逸 | 写入 `items/` 之外 |
| P0 | ID 分配与新增会无提示覆盖 | 已有条目永久丢失 |
| P0 | 派生 `blocked` 状态会被无关更新持久化 | 条目依赖完成后仍永久 blocked |
| P1 | 损坏条目被静默忽略 | Agent 把“读取失败”误判为“不存在” |
| P1 | JSON 模式并非稳定协议 | Agent JSON 解析随机失败 |
| P1 | 读操作会创建目录，显式路径会向父目录发现 | 查询产生写入或操作错项目 |
| P1 | `show`、`edit`、`index` 的项目目录解析不一致 | 同一 `--dir` 下命令看到不同数据 |
| P1 | 缺少并发控制和乐观锁 | 多 Agent 并发时覆盖更新或重复 ID |
| P2 | 参数和冲突组合未严格校验 | 拼写错误或矛盾意图仍返回成功 |
| P2 | Agent 需要手动维护派生索引 | 条目已更新但 `INDEX.md` 过期 |
| P2 | 项目注册表、文档与 ID 规则存在双重真相 | Agent 无法确定 project/prefix |
| P2 | `next` 混合 todo 与 in_progress | “下一项”可能其实是已开始任务 |

### P0：路径安全与数据覆盖

#### `project` 可逃逸 `items/`

`next_id()` 直接使用 `project_name[:3].upper()` 形成 ID，`add_item()` 再把 ID 拼入文件路径，
没有校验 ID 或确认最终路径仍位于 `items/`。

临时目录复现：

```text
backlog add -p ../evil ...
Created ../-001 → .../docs/backlog/items/../-001.md
```

实际文件写入了 `docs/backlog/-001.md`，已经逃逸 `items/`。同类问题也存在于外部传入的
`item_id`，因为 `show_item()`、`update_item()` 和 `edit()` 都直接拼接路径。

#### 同前缀项目会无提示覆盖

`next_id()` 只统计 `item.project == project_name` 的条目，但所有项目共享以 ID 命名的文件目录；
`add_item()` 使用普通 `write_text()`，不会拒绝已存在文件。

临时目录依次新增 `testing` 和 `tester` 后，两次都返回 `TES-001`，第二次无提示覆盖第一次，
最终仅剩一个 `TES-001.md`。

损坏文件也会触发同类覆盖：已有但无法解析的 `TES-001.md` 会被 `list_items()` 忽略，
随后 `add -p testing` 再次分配 `TES-001` 并覆盖损坏文件。

### P0：依赖阻塞污染持久化状态

`list_items()` / `show_item()` 会把未满足依赖的条目对象状态临时改成 `blocked`。
`update_item()` 又通过 `show_item()` 读取当前条目，并把整个模型重新写回文件。

复现流程：

1. `TES-002` 状态为 `todo`，依赖未完成的 `TES-001`。
2. 仅执行 `update TES-002 --title renamed`。
3. `TES-002.md` 的持久化状态被改为 `blocked`。
4. 完成 `TES-001` 后，`TES-002` 仍保持 `blocked`，不会自动恢复为 `todo`。

这直接违反 Agent contract 中“`update` 只修改明确传入字段”的承诺。

根因是“用户声明的生命周期状态”和“根据依赖计算的有效可执行状态”共用同一个字段。

### P1：读取结果不可信

#### 损坏条目静默消失

`_load_item()` 捕获所有解析和验证异常并返回 `None`；`list_items()` 直接跳过这些文件。
因此：

- `list --json` 对含损坏文件的 backlog 返回成功和空数组。
- `show <ID>` 把“条目损坏”报告成“条目不存在”。
- `stats`、`next`、`index` 都基于不完整数据继续成功执行。

Agent 无法区分“没有条目”和“数据未能读取”，还可能基于错误空结果继续新增或关闭任务。

#### 读操作隐式写文件系统

`list_items()` 和 `show_item()` 都调用会执行 `mkdir()` 的 `get_items_dir()`。
对一个全新目录执行 `list --json` 会返回 `[]`，同时创建 `docs/backlog/items/`。

这使只读 MCP tool 无法被标记为真正只读，也会让路径拼写错误悄悄创建新 backlog。

### P1：结构化输出协议不完整

目前只有 `list`、`next`、`stats` 部分支持 `--json`，且行为不一致：

- 空 backlog 的 `list --json` 返回 `[]`。
- 空 backlog 的 `next --json` 返回人类文本 `No active items to recommend.`，退出码仍为 0。
- `show`、`add`、`update`、`index` 没有 JSON 结果。
- 错误通过 Rich 文本输出，缺少稳定错误码、字段路径和机器可判断的错误类型。
- JSON 返回裸数组或裸统计对象，没有 `warnings`、解析失败信息、已解析项目根目录或版本信息。

因此 Agent 仍需解析终端文本和退出码，无法把 CLI 当成稳定 API。

### P1：项目目录语义不一致

显式传入 `--dir` 后，CRUD 会向父目录查找已有 `docs/backlog/`。这对人类从子目录运行命令方便，
但对 Agent/MCP 是隐式作用域扩张。

更严重的是各命令并不统一：

- `show --dir <project/subdir>` 可以向上找到项目根 backlog。
- `edit --dir <project/subdir>` 只检查 `<project/subdir>/docs/backlog/items`，报告同一条目不存在。
- `index --dir <project/subdir>` 从父级 backlog 读取内容，却把索引写到子目录的新
  `docs/backlog/INDEX.md`。
- `index --project` 参数存在但完全未使用。

Agent 传入相同 `--dir`，不同命令可能操作不同位置。

### P1：多 Agent 并发不安全

当前 ID 分配是“读取现有条目 → 计算下一个 ID → 普通写文件”，没有锁、独占创建或重试。
两个 Agent 同时新增条目时可能获得相同 ID，后写者覆盖先写者。

更新同样是“读取完整对象 → 修改 → 重写完整文件”，没有 revision / expected_updated 前置条件，
并发更新不同字段时仍可能互相覆盖。普通 `write_text()` 也不是原子替换，中断时可能留下半写文件。

### P2：参数成功不等于意图成功

临时目录复现：

- `list --priority P9 --json` 返回 `[]` 且退出码 0，拼写错误被解释成没有结果。
- `list --sort nonsense --json` 返回未排序数据且退出码 0。
- `update <ID> --status cancelled --fixed` 静默以 `--fixed` 为准，最终状态为 `done`。
- `--depends-on` 不校验目标存在、自依赖或依赖环。
- `add` 默认 `effort=M`、`impact=medium`，Agent 忘记评估时仍成功创建并进入评分队列。

这些行为对人类命令行尚可容忍，但会让 Agent 把错误调用当成成功决策。

### P2：Agent 操作负担与双重真相

- 每次写操作后，skill 要求 Agent 再调用 `index`；漏掉第二步会使派生索引过期。
- `project` 同时来自目标路径、`add -p` 参数、条目 frontmatter 和
  `~/.config/opencode/projects.json` key，容易不一致。
- 项目注册表提供 `backlog_prefix`，但 CLI 完全忽略它并使用项目名前三字符。
- 当前注册表中 `backlog-cli.backlog_prefix` 为 `BCK`，当前 Agent contract 示例为 `BAC`，
  CLI 对 `backlog-cli` 实际生成 `BAC`。
- 全局 skill 使用 `uv run --project`，Agent contract 使用 `uv run --directory`；两者虽都可工作，
  但增加了 Agent 判断成本。
- `next` 同时返回 `todo` 和 `in_progress`，没有区分“应恢复的任务”和“尚未开始的下一项”。

### 测试现状

现有质量门禁全部通过：

```text
pytest: 88 passed
ruff: All checks passed
pyright: 0 errors
```

现有测试准确覆盖了当前实现，却把部分危险行为固化为预期，例如：

- 损坏文件应返回 `None`。
- 读取时自动将依赖未满足条目标记为 `blocked`。
- `next` 空结果只检查退出码为 0，不检查 `--json` 协议。
- ID 测试明确忽略其他 `project`，但未测试共享前缀导致的文件覆盖。

因此增加 MCP 前，应先补充“Agent contract tests”，而不是只增加 MCP adapter tests。

## 建议决策方向

### 总体建议

采用以下目标架构：

```text
Backlog service/domain layer
├── strict storage adapter
├── CLI adapter（保留人类使用与兼容）
└── MCP adapter（Agent 主入口）
```

MCP Server 不应通过 subprocess 调用现有 CLI，也不应直接包装当前 `cli.py` 函数。
应先建立无终端概念、返回结构化结果的 service/domain layer，再让 CLI 与 MCP 复用。

### 阶段 1：先修复数据安全与核心语义

这是增加 MCP 层的阻塞项。

1. **统一项目上下文**
   - service 接收已解析的 `ProjectContext(root, project, prefix)`。
   - Agent/MCP 使用注册表 project key，不直接传任意 `--dir`、`project` 和 prefix 组合。
   - 显式 root 必须精确作用于该 root；仅 CLI 可提供可选的“从当前目录发现项目”便利功能。

2. **严格校验路径与 ID**
   - `project`、prefix、item ID 使用明确正则和长度限制。
   - 所有读写前确认解析后的路径仍位于目标 `items/`。
   - 新增使用独占创建，目标存在必须返回 conflict，禁止覆盖。

3. **修正 ID 分配**
   - prefix 以注册表配置为单一真相源。
   - 在整个 backlog 目录内按 prefix 分配，不按 frontmatter `project` 忽略同名文件。
   - 为“分配 ID + 创建文件”加锁并在冲突时重试。

4. **拆分声明状态与派生阻塞**
   - 建议持久化生命周期状态仅保留 `todo | in_progress | done | cancelled`。
   - `blocked` 改为派生字段，例如 `is_blocked`、`blocked_by`、`effective_status`。
   - 任何读取和无关更新都不得把派生状态写回。
   - 对依赖执行存在性、自依赖和环检测。

5. **错误必须显式**
   - 损坏条目返回带文件路径和验证原因的诊断；默认使调用失败。
   - 可另提供 `allow_partial=true`，但结果必须包含 `warnings` / `invalid_items`。
   - 读操作不得创建目录；增加显式 `init` 或在首次写入时创建。

6. **并发与原子性**
   - 新增使用锁和独占创建。
   - 更新支持 `expected_revision` 或内容 hash，冲突时返回明确错误。
   - 写入采用临时文件 + 原子替换。

### 阶段 2：建立 Agent 级结构化契约

在 MCP adapter 之前，先让 service 返回稳定 DTO；CLI 的 `--json` 可作为该契约的兼容验证入口。

建议统一成功结果：

```json
{
  "ok": true,
  "data": {},
  "warnings": [],
  "meta": {
    "project": "backlog-cli",
    "root": "/home/ling/ai/backlog-cli",
    "revision": "..."
  }
}
```

建议统一失败结果：

```json
{
  "ok": false,
  "error": {
    "code": "ITEM_CONFLICT",
    "message": "Item BAC-003 already exists",
    "details": {}
  }
}
```

契约要求：

- 所有命令均有结构化模式，空结果仍返回合法结构。
- 参数使用枚举和数组，不使用逗号分隔字符串。
- 未知 sort、负 limit、冲突参数必须校验失败。
- 写操作返回 `before`、`after`、`changed_fields` 和新 revision。
- 支持 `dry_run`，至少覆盖 update、状态变更和批量操作。
- 返回 `declared_status`、`effective_status`、`blocked_by` 和评分构成。
- `next` 默认只返回未开始的 `todo`；另行返回或查询 `in_progress`。

### 阶段 3：降低 Agent 操作负担

- 写操作成功后自动重建 `INDEX.md`，或明确把索引定义为可随时重建的派生文件并从 git 移除。
- MCP 不暴露 `edit`；正文直接作为字符串字段传入 `add` / `update`。
- MCP 不暴露日常 `index`；可保留管理员维护工具 `backlog_rebuild_index`。
- `add` 从 project context 推导 `project` 和 prefix，Agent 不再重复提供。
- 对默认 `effort` / `impact` 做明确决策：要求 Agent 显式传入，或在结果中返回
  `defaults_applied` 警告，避免默认值悄悄影响排序。

### 阶段 4：增加薄 MCP 层

建议首批 MCP tools：

| Tool | 说明 |
|---|---|
| `backlog_list_items` | 严格过滤、分页、结构化诊断 |
| `backlog_get_item` | 返回完整条目、依赖状态与 revision |
| `backlog_create_item` | 基于 project context 分配 ID，支持 dry run |
| `backlog_update_item` | patch 语义，支持 expected revision 与 dry run |
| `backlog_transition_item` | 显式状态变更，拒绝矛盾组合 |
| `backlog_get_work_queue` | 分开返回 in_progress、next todo、blocked |
| `backlog_get_stats` | 返回结构化统计和无效条目诊断 |
| `backlog_rebuild_index` | 管理/修复用途，不作为日常写后步骤 |

不建议把 `--fixed` 直接映射成 MCP 布尔参数。MCP 应使用明确动作
`transition_item(target_status="done")`，由 service 设置 `fixed_at`。

### 实施优先级

建议拆成三个实施批次：

1. **安全与正确性批次**：路径校验、禁止覆盖、严格解析、状态拆分、并发写安全。
2. **结构化 service 批次**：提取 domain/service、统一 DTO/错误、自动索引、Agent contract tests。
3. **MCP adapter 批次**：定义 tools schema、接入 project registry、补端到端测试，之后再简化全局 backlog skill。

在第 1、2 批次完成前，不建议把 MCP 层投入 Agent 日常使用。
