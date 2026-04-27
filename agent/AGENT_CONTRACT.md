# Backlog CLI — AI Agent 操作手册

本文档是 AI 使用 `backlog` 工具的标准契约。backlog 是一个轻量级任务管理 CLI，
每个条目对应一个 `docs/backlog/items/<ID>.md` 文件，使用 YAML frontmatter
承载结构化数据（优先级、分类、状态等），Markdown 正文承载描述。

## 1. 调用范式

```bash
uv run --directory ~/ai/backlog-cli backlog --dir <项目路径> <子命令>
```

`--dir` 指定目标项目根目录（必须包含或能自动创建 `docs/backlog/`）。

## 2. 命令速查

| 意图 | 命令 |
|------|------|
| 查看所有待办 | `list --status todo` |
| 按优先级过滤 | `list --priority P0,P1 --status todo` |
| 按分类过滤 | `list -c bug --status todo` |
| 查看推荐做啥 | `next -n 5` |
| 查看完整条目 | `show <ID>` |
| 开始做一条 | `update <ID> -s in_progress` |
| 标记完成 | `update <ID> --fixed` |
| 取消/关闭 | `update <ID> -s cancelled` |
| 为项目新增条目 | `add -p <project> -t "标题" -c <category> --priority P1 [-e S] [-i high]` |
| 更新条目字段 | `update <ID> --title "新标题" --priority P2` |
| 查看统计 | `stats` |
| 生成总览索引 | `index` |
| 编辑正文 | `edit <ID>` |

## 3. 数据模型

```yaml
id: "ZHI-001"          # 自动生成：项目名前3字母大写 + 三位序号
project: "zhijian"     # 项目标识
title: "标题"
category: feature      # bug | a11y | ux | i18n | testing | feature | refactor | perf | docs | architecture | security | research | ops
priority: P1           # P0(阻塞) > P1(严重) > P2(中等) > P3(低)
effort: M              # XS | S | M | L | XL
impact: high           # high | medium | low
status: todo           # todo | in_progress | done | cancelled | blocked
source: ""             # 来源标签（如 ui-audit-2026-04-27）
tags: [tag1, tag2]
depends_on: []         # 前置依赖的条目 ID
fixed_at: null         # 完成日期（--fixed 自动设为今天）
created: "2026-04-27"
updated: "2026-04-27"
```

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

高影响 + 小工作量的条目排最前。done/cancelled 条目 score=0，自动排除。

## 5. 操作约定

- **新增条目时必须提供**：`-p`（项目名）、`-t`（标题）、`-c`（分类）、`--priority`
- **ID 自动生成**：格式为 `<项目名前3字母大写>-<三位序号>`，如 zhijian→ZHI-001, inkborn→INK-001
- **更新字段**：`update` 只修改你明确传入的字段，其余保持不变
- **`--fixed` 是快捷方式**：等于 `-s done` + 自动填写 `fixed_at`
- **正文写作**：用 `-b "markdown内容"` 或之后用 `edit <ID>` 打开编辑器
- **JSON 输出**：`list --json` 可获得结构化数据供程序消费

## 6. 跨项目使用

工具本身是单体的，通过 `--dir` 参数切换目标项目。需要处理多个项目时：

```bash
# 操作 inkborn
uv run --directory ~/ai/backlog-cli backlog --dir ~/inkborn list --status todo

# 操作 ZhiJian
uv run --directory ~/ai/backlog-cli backlog --dir ~/ai/ZhiJian next -n 5
```

## 7. 常用组合

```bash
# 查看两个项目所有 P0/P1 待办
uv run --directory ~/ai/backlog-cli backlog --dir ~/inkborn list --priority P0,P1 --status todo
uv run --directory ~/ai/backlog-cli backlog --dir ~/ai/ZhiJian list --priority P0,P1 --status todo

# 做完一条并生成概览
uv run --directory ~/ai/backlog-cli backlog --dir ~/inkborn update INK-001 --fixed && \
uv run --directory ~/ai/backlog-cli backlog --dir ~/inkborn index
```
