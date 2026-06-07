# Backlog CLI — AI Agent 操作手册

本文档是 AI 使用 `backlog` 工具的标准契约。backlog 是一个轻量级任务管理 CLI，
每个条目对应一个 `docs/backlog/items/<ID>.md` 文件，使用 YAML frontmatter
承载结构化数据（优先级、分类、状态等），Markdown 正文承载描述。

## 0. 前置：项目发现

**不要硬编码项目名或路径。** 每次会话应从 `~/.config/opencode/projects.json` 动态获取：

```json
// 文件格式（示例）
{
  "projects": {
    "inkborn": { "path": "~/inkborn",              "backlog_prefix": "INK", ... },
    "zhijian": { "path": "~/ai/ZhiJian",           "backlog_prefix": "ZHI", ... },
    "backlog-cli": { "path": "~/ai/backlog-cli",   "backlog_prefix": "BCK", ... }
  }
}
```

- `path` → `--dir` 参数值（展开 `~` 为 `$HOME`）
- 新增项目时，向用户确认后写入该 JSON 文件
- 批量操作时，遍历 `projects` 的 key/value 逐项执行

## 1. 调用范式

```bash
uv run --directory ~/ai/backlog-cli backlog --dir <项目路径> <子命令>
```

`--dir` 指定目标项目根目录。若该目录或其父级已有 `docs/backlog/`，工具会复用；
否则会在 `--dir` 指向的目录下创建 `docs/backlog/`。

## 2. 命令速查

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
| 为项目新增条目 | `add -p <project> -t "标题" -c <category> --priority P1 -b "一句话描述" [-e S] [-i high]` |
| 新增条目(多行正文) | `add -p <project> -t "标题" -c <category> --priority P1 --body-file /tmp/body.md` |
| 通过管道新增 | `cat body.md \| backlog add ... --stdin` |
| 更新条目字段 | `update <ID> --title "新标题" --priority P2 [-b "更新后的描述"]` |
| 替换正文(文件) | `update <ID> --body-file /tmp/body.md` |
| 替换正文(管道) | `cat body.md \| backlog update <ID> --stdin` |
| 编辑正文(管道) | `cat body.md \| backlog edit <ID> --stdin` |
| 查看统计 | `stats` |
| 查看统计 (JSON) | `stats --json` |
| 生成总览索引 | `index` |
| 编辑正文(交互) | `edit <ID>` |

## 3. 数据模型

```yaml
id: "ZHI-001"          # 自动生成：项目名前3字母大写 + 三位序号
project: "zhijian"     # 项目标识
title: "标题"
category: feature      # bug | a11y | ux | i18n | testing | feature | refactor | perf | docs | architecture | security | research | ops
priority: P1           # P0(最高/影响发布) > P1(严重) > P2(中等) > P3(低)
effort: M              # XS | S | M | L | XL
impact: high           # high | medium | low
status: todo           # todo | in_progress | done | cancelled | blocked
source: ""             # 来源标签（如 ui-audit-2026-04-27）
tags: [tag1, tag2]
depends_on: []         # 前置依赖的条目 ID
fixed_at: null         # 完成日期；仅 status=done 时有效（--fixed 自动设为今天）
created: "2026-04-27"
updated: "2026-04-27"
```

`body` 字段存于 YAML frontmatter 之外（Markdown 正文），不在 frontmatter 区块内。
它是条目的「为什么 / 做什么 / 如何验证」描述，`show <ID>` 会完整展示。

## 4. 排序规则

`next` 和 `list --sort score` 使用的推荐分数：

```
score = priority_weight × impact_weight × effort_weight
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

## 5. 操作约定

- **新增条目时必须提供**：`-p`（项目名）、`-t`（标题）、`-c`（分类）、`--priority`
- **强烈建议同时提供 `-b "描述"`**：一句话说明条目的背景/目的/验收标准，让 `show` 和 `--json` 输出有实质内容。无描述的条目在列表视图中信息密度过低
- **ID 自动生成**：格式为 `<项目名前3字母大写>-<三位序号>`，如 zhijian→ZHI-001, inkborn→INK-001
- **更新字段**：`update` 只修改你明确传入的字段，其余保持不变
- **`--fixed` 是快捷方式**：等于 `-s done` + 自动填写 `fixed_at`
- **状态机约束**：`fixed_at` 只属于 `done`。当条目从 `done` 改回 `todo` / `in_progress` / `blocked` / `cancelled` 时，工具会清除 `fixed_at`
- **依赖阻碍状态解耦**：具有未完成前置依赖的条目，在读取/渲染视图时其有效状态（`effective_status`）为 `blocked`，但在底层 Markdown 数据中仍保持其声明状态（如 `todo` / `in_progress`）。对该条目的更新操作不会将计算出的临时 `blocked` 状态持久化固化，当前置依赖完成时，该任务将自动恢复为正常的可进行状态。
- **结构化 JSON 契约**：所有带有 `--json`（或 `--format json`）选项的命令成功执行时，输出包装在 `{"ok": true, "data": ...}` 内；执行失败或遇严重错误时，输出包装在 `{"ok": false, "error": {"code": "错误码", "message": "错误信息", "details": {}}}` 内。常见的错误码包括 `PARSING_ERROR`（条目损坏）、`ITEM_CONFLICT`（ID 重复覆盖冲突）、`ITEM_NOT_FOUND`（条目不存在）及 `INVALID_INPUT`（非法参数输入，包括非法 sort、非负 limit 或 priority 校验等 ClickException 错误，都会在 JSON 模式下由此统一结构化输出，并不直接抛出人类终端文本）。
- **正文写作**：
  - 单行简短描述：`-b "一句话描述"` 即可
  - 多行正文：用 `--body-file /tmp/body.md` 或 Heredoc + `--stdin`
  - 示例：`cat <<'EOF' | backlog add -p myproj -t "标题" -c feature --priority P1 --stdin ... EOF`
  - **禁止**：试图通过 `-b "line1\nline2"` 传递多行 — `\n` 不会被 shell 展开
  - **通道安全**：`--stdin` 和 `--body-file` 为二进制安全通道，可安全传递任意 Markdown（含代码块、ASCII 表格、反引号 `` ` ``、管道符 `|` 等特殊字符）。`-b` 走 shell 参数解析，特殊字符需转义，仅适用于单行纯文本
- **非 TTY 环境**：`edit <ID>` 会报错退出；应改用 `edit <ID> --stdin` 或 `update <ID> --stdin`
- **`next --json`**：返回与 `list --json` 一致的结构化输出，供 Agent 解析推荐列表
- **并发控制与版本锁 (Revision)**：每个条目均在 Frontmatter 包含一个 `revision` 数据指纹（由 UUID 构成）。使用 `update` 修改条目时，可传入 `--expected-revision <REV>` 进行乐观锁校验，若当前条目版本不一致将报错 `Revision mismatch` 拒绝修改。
- **安全锁内自动 ID 分配**：新增条目时无需前置在外部计算 ID，底层在 `_lock_backlog` 锁中计算 `next_id()`，能保证在多 Agent 并发写入时，绝对不分配出重复的 ID，也绝对不发生写入覆盖冲突。

## 6. 跨项目使用

工具本身是单体的，通过 `--dir` 参数切换目标项目。项目列表应从 `~/.config/opencode/projects.json` 动态读取（见第 0 节）：

```bash
# 读取项目列表
cat ~/.config/opencode/projects.json

# 对每个项目执行操作（伪代码）
for name, info in projects.items:
    uv run --directory ~/ai/backlog-cli backlog --dir ${info.path} list --status todo
```

操作前先 `git status --short` 检查目标项目工作区是否干净，避免冲突。

## 7. 常用组合

```bash
# 查看所有项目 P0/P1 待办（从 projects.json 动态获取路径）
for path in $(python3 -c "import json; [print(v['path'].replace('~/','$HOME/')) for v in json.load(open('$HOME/.config/opencode/projects.json'))['projects'].values()]"); do
  uv run --directory ~/ai/backlog-cli backlog --dir "$path" list --priority P0,P1 --status todo
done

# 做完一条（后台将自动重建概览索引 INDEX.md）
uv run --directory ~/ai/backlog-cli backlog --dir <project_path> update <ID> --fixed

# 新增条目（含多行正文 — Heredoc + --stdin）
cat <<'EOF' | uv run --directory ~/ai/backlog-cli backlog --dir <project_path> add \
  -p <project> -t "标题" -c <category> --priority P1 -e S -i high --stdin
## 背景

为什么要做这条。

## 目标

- 具体做什么
- 如何验证完成
EOF

# 替换正文（管道）
cat /tmp/new-body.md | uv run --directory ~/ai/backlog-cli backlog --dir <project_path> edit <ID> --stdin

# 设置前置依赖
uv run --directory ~/ai/backlog-cli backlog --dir <project_path> update <ID> --depends-on INK-001,INK-002

# 获取推荐列表 JSON
uv run --directory ~/ai/backlog-cli backlog --dir <project_path> next -n 3 --json
```
