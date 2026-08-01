---
name: backlog
description: |
  管理 portable Backlog Store 的 Agent 契约。通过 host 的 Catalog resolver 获取 exact store，
  以 JSON 调用 list、show、add、update、next。
license: MIT
metadata:
  audience: developer
  framework: backlog-cli
---

# Backlog — Agent 操作契约

本文档是 repo-owned 的唯一 Agent skill authority。全局 skill 入口只能通过 symlink 指向本目录；
旧链接由 `agent/AGENT_CONTRACT.md` 跳转到这里。

`backlog` 是 Git-native、Markdown-first 的 portable task-intent ledger。一个
`backlog/Store@1` 保存跨会话的任务意图、验收边界、声明状态、依赖和 revision；它不负责项目路由、
执行调度或任务是否真的完成。

## 触发时机

仅在用户要求查询、创建或更新 backlog，或当前工作明确对应 backlog ID 时加载本 skill。普通问答不主动
查询或修改 backlog。

## Agent 官方快速路径

Host 先通过 Catalog resolver 取得 exact `backlog/store@1` root，再调用 CLI；Workspace Control 负责从项目
或 workspace 上下文解析该 root，backlog-cli 不读取 Catalog，也不猜测路径。

```bash
/home/ling/workspace/workspace-control/bin/workspace project resolve <target> \
  --catalog /home/ling/workspace/workspace-control/catalog/workspace.json --json
```

成功 envelope 的 project scope 使用 `data.artifacts.backlog.root` 作为 exact store。它必须是绝对路径，且已
包含有效 `backlog.json`、`items/` 和 `INDEX.md`。无唯一 resolved context 或 store 无效时停止并报告；不要
改用目录名、parent、child、Repo 或 Project Ops 路径。

所有官方调用都使用：

```bash
backlog --store <absolute-backlog-root> <子命令> --json
```

官方快速路径只包含 `list`、`show`、`add`、`update`、`next` 五个操作：

| 意图 | JSON 调用 |
|---|---|
| 查询条目 | `list --status todo --json` |
| 获取完整条目 | `show <ID> --json` |
| 创建条目 | `add -T "标题" -c <category> --priority P1 --body-file /tmp/body.md --json` |
| patch 条目 | `update <ID> --status in_progress --expected-revision <REV> --json` |
| 获取工作队列 | `next -n 5 --json` |

`--store` 放在子命令前。官方调用只消费 JSON envelope。

## Agent 契约：JSON、revision 与状态

成功结果为 `{"ok": true, "data": ...}`，退出码为 0。操作失败为
`{"ok": false, "error": {"code": "...", "message": "...", "details": {}}}`，退出码为 1；CLI usage
error 退出码为 2。常见稳定错误码有 `STORE_INVALID`、`INVALID_INPUT`、`ITEM_NOT_FOUND`、
`ITEM_CONFLICT`、`PARSING_ERROR` 和 `REVISION_MISMATCH`。

`add` 与 `update` 的 `data` 是 mutation receipt，包含 `before`、`result`、`changed_fields`、`revision`、
`no_op` 和 store identity。读取条目取得 revision 后，使用
`update <ID> --expected-revision <REV> ... --json` 防止覆盖并发修改；`no_op: true` 表示没有写入 item 或 index。

`status` 是持久化的声明状态。`effective_status` 与 `blocked_by` 是读取时依据未完成或缺失依赖派生的视图，
不应由 Agent 持久化。`list --json` 与 `show --json` 都返回三者；`next --json` 返回 `queue_state`
（`in_progress`、`ready` 或 `blocked`）和 `blocked_by`。`next` 的 `score_hint` 只解释兼容排序，不能用于
编排或完成度判断。

<a id="Backlog-Item-Body-Contract"></a>

## Agent 责任：正文完整性

Backlog item 的 Markdown body 是任务意图与验收边界的 authority。准备交付执行的 item 至少应包含：

```markdown
## Intent

说明 what/why 与完成后的可观察变化。

## Acceptance Criteria

- 列出可验证的通过或失败结果，以及必要的边界。
```

有实际信息时才补充 `Boundaries` 或 `Decision Boundaries`。正文描述目的和可观察结果，不预先规定内部
实现。CLI 只保存正文，不机械判定其是否可执行；派发该 item 的 host、workflow 或 reviewer 负责确认
目标和验收边界足够完整。单行 `-b` 只适合快速捕获，使用 `--body-file` 或 `--stdin` 传递多行正文。

## 按需参考：人类、管理员与兼容入口

以下内容不属于 Agent 官方快速路径，按需要再读取或使用：

| 类别 | 保留能力 | 用途 |
|---|---|---|
| 人类输出 | Rich table、CSV、非 JSON `next` | 交互式浏览和兼容推荐 |
| 人类快捷方式 | `edit`、`update --fixed` | TTY 编辑；`--fixed` 等同完成快捷方式，Agent 应明确用 `--status done` |
| 管理与维护 | `stats`、`index`、`provision-store`、`validate-store` | 概览、索引修复、legacy store manifest provisioning/validation |
| 低频字段 | `effort`、`impact`、`score`、`extra`、`tags`、`source`、`related_docs` | 可选检索或兼容元数据，不是 Agent 创建任务时的默认决策 |
| compatibility | `--target` 与 CWD discovery | 既有调用的 outer adapter，先解析为 exact store 后才进入同一 core |

`backlog.json` 的 `schema`、`project_id` 与 `id_prefix` 是 exact store identity 的 authority。item 的
`project` 和 ID prefix 必须与 manifest 一致；Agent 不预先计算 ID。`depends_on` 与 `related_docs` 可在确有
事实时设置，前者用条目 ID，后者使用 Repo 相对路径或 `project-ops:` 逻辑引用。

未注册的独立 Repo 可以使用 legacy discovery；已注册项目不得这样做。管理员处理 legacy store 时，可先用
`--target`/CWD adapter 执行 `validate-store`，再用 `provision-store --project-id <id> --id-prefix <prefix>` 创建
manifest。不要以此为新 Agent integration 新增路径猜测分支。

完整的人类安装、示例与产品边界见 [README.md](../../README.md)，实现和数据流见
[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)。
