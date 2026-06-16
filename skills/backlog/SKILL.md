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

## 触发时机

当任务涉及 backlog 管理时加载本 skill：
- 用户要求「记录一个待办」「帮我记一下」「加到 backlog」
- 用户要求「看看还有什么没做完」「还有哪些 bug」「推荐做啥」
- 用户要求「标记完成」「更新状态」「设置优先级」
- 用户要求「查看 backlog 统计」「生成索引」
- 当前开发任务明确对应某个 backlog ID，需要开始、完成或更新该条目
- 在项目开发过程中确认发现了新的 bug、技术债或后续任务，且值得跨会话保留

不要因为普通问答或只读解释自动查询 backlog；只有进入项目开发、计划、任务选择或 backlog CRUD 场景时才主动操作。

## 核心原则

加载本 skill 后，先用 `project-ops list --json` 获取 `id == "backlog-cli"` 的
`data.projects[]` 条目，将其 `repo` 记为 `<backlog-cli-repo>`，再读取
`<backlog-cli-repo>/agent/AGENT_CONTRACT.md` 获取完整契约。该文档是单一源头，包含：
- 全部子命令与选项
- 数据模型与字段
- 评分排序规则
- 操作约定与禁区

本 skill 只定义调用范式和自动行为边界；命令细节以 `AGENT_CONTRACT.md` 为准。

条目正文位于 `<ops-path>/backlog/items/<ID>.md`，便于 review 和搜索；CLI 负责 ID 生成、frontmatter 更新、状态流转、统计和 `INDEX.md` 重建。

如果 CLI 不可用，才回退为直接编辑 `<ops-path>/backlog/items/*.md`；回退后必须用等价方式同步 `<ops-path>/backlog/INDEX.md`，并在回复中说明未能使用 CLI。

## Project Ops 路径解析

所有 backlog 操作前，运行：

```bash
project-ops resolve <目标路径> --json
```

确认输出 `ok` 为 `true`，将 `data.ops` 记为 `<ops-path>`，将 `data.repo` 记为目标 Repo。
命令返回 `PROJECT_NOT_FOUND` 或其他错误时停止，不得回退到 Repo 内创建
`docs/backlog/`。不得自行读取注册表、解释 `workspace_root`、拼接路径或按目录名猜测项目。

## 调用范式

优先使用：

```bash
backlog --target <ops-path> <子命令>
```

`--target` 必须指定注册表解析出的 `<ops-path>`。backlog-cli 会在其下管理 `backlog/`。

若 PATH 中的 `backlog` 入口不可用或模块环境损坏，先用 `project-ops list --json` 获取
`id == "backlog-cli"` 的 `repo`，再使用：

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --project <backlog-cli-repo> backlog --target <ops-path> <子命令>
```

`<backlog-cli-repo>` 必须来自稳定 JSON 契约，不得使用历史固定路径。若 `project-ops` CLI
不可用则停止并报告，不能回退实现注册表解析；若 backlog fallback 也不可用，才回退为
直接编辑 backlog 文件。

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

只有当当前任务明确对应某个 backlog ID，且代码/文档/测试验收已完成时，才标记完成：

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

若正文较长，使用 `--body-file /tmp/body.md` 或 `--stdin`，避免把多行 Markdown 塞进 `-b`。

### 查看整体进度

用户关注进度，或完成一组 backlog 操作后：

```bash
backlog --target <ops-path> stats
```

## 常用命令

```bash
# 列出 todo
backlog list --status todo

# 推荐下一批
backlog next -n 5

# 查看条目
backlog show <ID>

# 新增条目
backlog add -T "标题" -c feature --priority P1 -e M -i high -b "一句话描述"

# 更新字段
backlog update <ID> --priority P2

# 完成条目并重建索引
backlog update <ID> --fixed
backlog index
```
