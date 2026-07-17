---
name: backlog
description: |
  backlog 任务管理工具。当需要读取、记录、更新项目待办条目时使用。
  通过项目注册表解析出的 backlog-cli 操作 Project Ops 中的 backlog 条目与索引，
  提供 list/show/add/update/stats/next/index/edit 子命令，
  支持按优先级、状态、分类过滤和评分排序。
license: MIT
metadata:
  audience: developer
  framework: backlog-cli
---

# Backlog — CLI 任务管理

本文档是 AI Agent 使用 `backlog` 工具的标准契约，也是 repo-owned skill 的单一源头。
全局 skill 入口应通过 symlink 指向本目录，避免在全局配置中复制维护另一份说明。

`backlog` 是轻量级任务管理 CLI。每个条目对应
`<backlog-root>/items/<ID>.md`，使用 YAML frontmatter 承载结构化数据，Markdown 正文承载
任务意图与验收边界。CLI 负责 ID 生成、frontmatter 更新、状态流转、统计和 `INDEX.md`
重建。

## 触发时机

当任务涉及 backlog 管理时加载本 skill：
- 用户要求「记录一个待办」「帮我记一下」「加到 backlog」
- 用户要求「看看还有什么没做完」「还有哪些 bug」「推荐做啥」
- 用户要求「标记完成」「更新状态」「设置优先级」
- 用户要求「查看 backlog 统计」「生成索引」
- 当前开发任务明确对应某个 backlog ID，需要开始、完成或更新该条目
- 在项目开发过程中确认发现了新的 bug、技术债或后续任务，且值得跨会话保留

不要因为普通问答或只读解释自动查询 backlog；只有进入项目开发、计划、任务选择或 backlog
CRUD 场景时才主动操作。

## Project Ops 路径解析

所有已注册项目的 backlog 操作前，按 `/home/ling/workspace/AGENTS.md` 的项目注册表规则读取
`/home/ling/workspace/project-ops/projects.json`。将唯一匹配记录的 `ops` 记为 `<ops-path>`，
将 `repo` 记为目标 Repo，并读取目标 Repo 的 `AGENTS.md`。无匹配或匹配不唯一时停止，不得
回退到 Repo 内创建 `docs/backlog/`。

只有处理未注册的独立 Repo 时，才允许依赖 `backlog` 从当前工作目录向上查找
`docs/backlog/`。

## 调用范式

优先使用：

```bash
backlog --target <ops-path> <子命令>
```

`--target` 必须指定注册表解析出的 `<ops-path>`。backlog-cli 会在其下管理 `backlog/`。

若 PATH 中的 `backlog` 入口不可用或模块环境损坏，从同一 registry 获取
`id == "backlog-cli"` 的唯一记录，将其 `repo` 记为 `<backlog-cli-repo>`，再使用：

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --project <backlog-cli-repo> backlog --target <ops-path> <子命令>
```

`<backlog-cli-repo>` 必须来自 registry，不得使用历史固定路径。若 backlog fallback 也不可用，
才回退为直接编辑 `<ops-path>/backlog/items/*.md`。回退后必须用等价方式同步
`<ops-path>/backlog/INDEX.md`，并在回复中说明未能使用 CLI。

## 命令速查

| 意图 | 命令 |
|------|------|
| 查看所有待办 | `list --status todo` |
| 按优先级过滤 | `list --priority P0,P1 --status todo` |
| 按分类过滤 | `list -c bug --status todo` |
| 查看推荐做啥 | `next -n 5` |
| 查看推荐 (JSON) | `next -n 5 --json` |
| 查看完整条目 | `show <ID>` |
| 开始做一条 | `update <ID> -s in_progress` |
| 标记完成 | `update <ID> --fixed` |
| 取消/关闭 | `update <ID> -s cancelled` |
| 设置依赖关系 | `update <ID> --depends-on INK-001,INK-002` |
| 设置相关文档 | `update <ID> --related-docs docs/ARCHITECTURE.md,project-ops:plans/plan.md` |
| 新增快速记录 | `add -T "标题" -c <category> --priority P1 -b "一句话描述" [-e S] [-i high]` |
| 新增可执行条目 | `add -T "标题" -c <category> --priority P1 --body-file /tmp/body.md` |
| 通过管道新增 | `cat body.md \| backlog add ... --stdin` |
| 更新条目字段 | `update <ID> --title "新标题" --priority P2 [-b "更新后的描述"]` |
| 替换正文(文件) | `update <ID> --body-file /tmp/body.md` |
| 替换正文(管道) | `cat body.md \| backlog update <ID> --stdin` |
| 编辑正文(管道) | `cat body.md \| backlog edit <ID> --stdin` |
| 查看统计 | `stats` |
| 查看统计 (JSON) | `stats --json` |
| 生成总览索引 | `index` |
| 编辑正文(交互) | `edit <ID>` |

## Backlog Item Body Contract

Backlog item 是任务意图与验收边界的权威来源，不预先描述具体 Agent、运行阶段或调度方式。
Agent 创建或补全一个准备交付执行的 item 时，正文默认包含 `Intent` 和
`Acceptance Criteria`：

```markdown
## Intent

说明当前问题、为什么值得处理，以及完成后应产生的可观察变化。描述 what/why，不预先规定
how。

## Acceptance Criteria

- 列出 Reviewer 可以明确判断通过或失败的结果。
- 根据任务风险覆盖必要的成功行为、失败行为和关键边界。
```

只有存在实际信息时才添加以下可选章节，不为模板完整性创建空章节：

```markdown
## Boundaries

- 记录必须保持的兼容行为、明确排除的范围或不可违反的约束。

## Decision Boundaries

- 说明 Agent 可以自主决定的事项。
- 说明哪些情况必须停止并向用户报告或请求决策。
```

正文生成规则：

- `Intent` 描述稳定的目的和结果，不把暂定实现思路、文件列表、工具选择或执行步骤写成要求。
- `Acceptance Criteria` 使用可观察、可验证的结果；除非实现方式本身是已接受的架构约束，
  不规定类名、函数名、算法或内部模块划分。
- 已确定的架构决策和计划通过 `related_docs` 引用，优先使用逻辑路径和 section
  anchor；正文只提取直接影响本 item 的边界，不复制整份文档。
- 条目依赖使用 `depends_on` 表达。仅当依赖如何影响当前任务并不明显时，才在正文补充说明。
- Repo `AGENTS.md` 中的默认测试、文档同步和质量门禁自动适用；只有需要增加、缩小或偏离
  默认要求时才写入正文。
- 不编造缺失的产品或架构决策。若缺失信息会实质改变目标、验收边界或公开契约，先向用户
  询问；否则保留给执行 Agent 判断。

一句话 `-b` 适合快速捕获尚未充分定义的 seed、提醒或后续调查线索。它本身不代表 item 已
达到可执行标准。准备交给 Agent 或 orchestrator 执行时，使用 `--body-file` 或 `--stdin`
写入上述最小正文。

判断 item 是否足以执行时，不按正文长度或可选章节数量判定，而检查：

1. 具备项目所需能力的 Agent 能否在不追问已有事实的情况下开始工作；
2. Reviewer 能否根据 item 与 `related_docs` 判断通过或失败；
3. 真正不可违反的边界是否明确；
4. 未被锁定的实现选择是否仍留给执行 Agent。

CLI 当前只负责保存正文，不机械验证这些语义要求。消费 item 的 workflow 或 orchestrator 应在
派发前执行完整性检查，并拒绝目标或验收边界不足的条目。

## 自动行为边界

Agent 可在以下场景主动操作 backlog，但必须满足对应前提。

### 进入项目开发或任务选择

当用户要求选择下一项任务、查看项目待办、进入开发规划，或当前请求明显需要 backlog 上下文时：

```bash
backlog --target <ops-path> list --status todo
```

或查看推荐：

```bash
backlog --target <ops-path> next -n 5
```

### 开始处理已知条目

只有当当前任务明确对应某个 backlog ID 时，才标记进行中：

```bash
backlog --target <ops-path> update <ID> -s in_progress
backlog --target <ops-path> index
```

### 完成已知条目

只有当当前任务明确对应某个 backlog ID，且代码、文档、测试验收已完成时，才标记完成：

```bash
backlog --target <ops-path> update <ID> --fixed
backlog --target <ops-path> index
```

不要因为完成了一个临时用户请求而猜测性关闭 backlog 条目。

### 记录新问题或技术债

只有当 bug、技术债或后续任务已经被确认，且值得跨会话保留时才新增条目：

```bash
backlog --target <ops-path> add -T "描述" -c <bug|refactor|perf|feature|ux|docs> --priority <P1|P2> -e <XS|S|M|L|XL> -i <high|medium|low> -b "一句话说明背景、目标或验收标准"
backlog --target <ops-path> index
```

该形式用于快速捕获。如果问题已经足够明确并准备交付执行，按
`Backlog Item Body Contract` 编写多行正文，并使用 `--body-file /tmp/body.md` 或 `--stdin`，
避免把多行 Markdown 塞进 `-b`。

### 查看整体进度

用户关注进度，或完成一组 backlog 操作后：

```bash
backlog --target <ops-path> stats
```

## 数据模型

```yaml
id: "ZHI-001"          # 自动生成：项目目录名前3字母大写 + 三位序号
project: "zhijian"     # 项目标识（自动取自目标目录名）
title: "标题"
category: feature      # bug | a11y | ux | i18n | testing | feature | refactor | perf | docs | architecture | security | research | ops
priority: P1           # P0(最高/影响发布) > P1(严重) > P2(中等) > P3(低)
effort: M              # XS | S | M | L | XL
impact: high           # high | medium | low
status: todo           # todo | in_progress | done | cancelled | blocked
source: ""             # 来源标签（如 ui-audit-2026-04-27）
tags: [tag1, tag2]
depends_on: []         # 前置依赖的条目 ID
related_docs: []       # 相关文档逻辑引用，如 docs/ARCHITECTURE.md 或 project-ops:plans/plan.md
fixed_at: null         # 完成日期；仅 status=done 时有效（--fixed 自动设为今天）
created: "2026-04-27"
updated: "2026-04-27"
```

`body` 字段存于 YAML frontmatter 之外，不在 frontmatter 区块内。`show <ID>` 会完整展示正文。

## 排序规则

`next` 和 `list --sort score` 使用的推荐分数：

```text
score = priority_weight * impact_weight * effort_weight
```

| Priority | Weight | Impact | Weight | Effort | Weight |
|----------|--------|--------|--------|--------|--------|
| P0 | 100 | high | 3 | XS | 10.0 |
| P1 | 50 | medium | 2 | S | 5.0 |
| P2 | 10 | low | 1 | M | 2.0 |
| P3 | 1 | | | L | 1.0 |
| | | | | XL | 0.5 |

高影响 + 小工作量的条目排最前。done/cancelled 条目 score=0。

- `list --sort score`：按 score 展示所有匹配条目，包含 `blocked`
- `next`：只推荐 `todo` / `in_progress`，排除 `blocked`、`done`、`cancelled`
- `list --json` 与 `next --json`：每个条目包含 `score` 字段，供 Agent 解释推荐依据

## 操作约定

- 新增条目时必须提供 `-T`（标题）、`-c`（分类）、`--priority`（优先级）。项目名自动从目标目录名推导。
- 快速捕获 seed、提醒或调查线索时可使用 `-b "描述"`；准备交付执行的 item 按
  `Backlog Item Body Contract` 使用多行正文。
- ID 自动生成：格式为 `<目录名前3字母大写>-<三位序号>`，如 `zhijian/` 目录生成 `ZHI-001`。
- `update` 只修改明确传入的字段，其余保持不变。
- `related_docs` 保存与条目相关的 Repo 文档或 Project Ops artifact 逻辑引用。`--related-docs` 传入逗号分隔列表并替换整个列表。CLI 只做 trim/去空，不检查路径是否存在。
- `--fixed` 是快捷方式，等于 `-s done` + 自动填写 `fixed_at`。
- `fixed_at` 只属于 `done`。当条目从 `done` 改回 `todo` / `in_progress` / `blocked` / `cancelled` 时，工具会清除 `fixed_at`。
- 具有未完成前置依赖的条目，在读取/渲染视图时有效状态 `effective_status` 为 `blocked`，但底层 Markdown 数据仍保持其声明状态。更新操作不会将计算出的临时 `blocked` 状态持久化。
- 所有带 `--json`（或 `--format json`）选项的命令成功时输出 `{"ok": true, "data": ...}`；失败时输出 `{"ok": false, "error": {"code": "...", "message": "...", "details": {}}}`。常见错误码包括 `PARSING_ERROR`、`ITEM_CONFLICT`、`ITEM_NOT_FOUND`、`INVALID_INPUT`。
- 单行快速记录使用 `-b "一句话描述"`；可执行正文使用 `--body-file /tmp/body.md` 或
  `--stdin`。
- 禁止通过 `-b "line1\nline2"` 传递多行，`\n` 不会被 shell 展开。
- `--stdin` 和 `--body-file` 为二进制安全通道。
- 非 TTY 环境中 `edit <ID>` 会报错退出；应改用 `edit <ID> --stdin` 或 `update <ID> --stdin`。
- `next --json` 返回与 `list --json` 一致的结构化输出，供 Agent 解析推荐列表。
- 每个条目均在 frontmatter 包含 `revision` 数据指纹。使用 `update` 修改条目时，可传入 `--expected-revision <REV>` 进行乐观锁校验。
- 新增条目时无需前置在外部计算 ID，底层在安全锁内计算 `next_id()`，能避免多 Agent 并发写入时分配重复 ID。

## 跨项目使用

通过 `--target` 参数切换目标。已注册项目先按 Workspace `AGENTS.md` 的项目注册表规则取得
记录，并将其中的 `ops` 作为 `<ops-path>`。不得拼接 Project Ops 路径或使用固定路径 fallback。

```bash
backlog --target <ops-path> stats --json
```

操作前先 `git status --short` 检查目标项目工作区状态，避免覆盖用户正在编辑的内容。

## 常用组合

```bash
# 列出 todo
backlog --target <ops-path> list --status todo

# 推荐下一批
backlog --target <ops-path> next -n 5

# 查看条目
backlog --target <ops-path> show <ID>

# 新增快速记录
backlog --target <ops-path> add -T "标题" -c feature --priority P1 -e M -i high -b "一句话描述"

# 新增可执行条目（正文包含 Intent 与 Acceptance Criteria）
backlog --target <ops-path> add -T "标题" -c feature --priority P1 --body-file /tmp/body.md

# 更新字段
backlog --target <ops-path> update <ID> --priority P2

# 完成条目并重建索引
backlog --target <ops-path> update <ID> --fixed
backlog --target <ops-path> index

# 获取推荐列表 JSON
backlog --target <ops-path> next -n 3 --json
```
