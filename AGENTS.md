# AGENTS.md — Backlog CLI 开发路由中枢

> 人类开发者请阅读 [README.md](README.md)。外部 AI 如需**使用** backlog 工具：
> - 读取 repo-owned skill [skills/backlog/SKILL.md](skills/backlog/SKILL.md)
> - 全局 skill 入口应通过 symlink 指向该目录；不要复制维护另一份说明
> - 旧入口 [agent/AGENT_CONTRACT.md](agent/AGENT_CONTRACT.md) 仅保留为兼容跳转

## 绝对红线

- 语言：Python 3.12+
- 依赖最小化：Typer (CLI) + Pydantic (模型) + python-frontmatter (序列化) + Rich (输出) + PyYAML
- 每层只做一件事：models (数据) → items (存储) → cli (展示)
- 文件系统存储，不引入数据库
- 变更后运行 `uv run --group dev python -m pytest && uv run --group dev ruff check . && uv run --group dev python -m pyright` 确保通过

## 路由表

| 文档 | 何时读 | 何时更新 |
|------|--------|----------|
| [skills/backlog/SKILL.md](skills/backlog/SKILL.md) | 外部 AI 学习如何使用 backlog 工具 | 新增/修改 CLI 命令、数据字段、排序规则后 |
| [agent/AGENT_CONTRACT.md](agent/AGENT_CONTRACT.md) | 旧链接兼容入口 | skill 权威入口路径变化后 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 修改模块结构、引入新依赖、改变存储方案 | 组件关系变更、新增模块、技术选型变化 |
| Catalog resolved `artifacts.adr.root` | 查看产品定位、架构边界和候选方案取舍时 | 新增、替代或废弃架构决策时 |
| Project resolved `artifacts.backlog.root` 或已校验的 workspace `sources.workspace_artifacts.root/backlog` | 查看任务状态与验收范围 | 通过 backlog CLI 增改条目时 |
| Catalog resolved `artifacts.plans.root` | 讨论项目长期演进、Agent 原生任务生命周期、skills/subagents 或独立控制平面时 | 新增或更新执行计划时 |
| Catalog resolved `artifacts.research.root` | 查阅项目级调研证据时 | 完成项目级调研时 |
| `backlog --store <resolved exact backlog root> list --status todo --json` | **进入项目时自动执行**；寻找下一个开发任务 | 完成条目后使用 `update <ID> --status done --expected-revision <REV> --json` 标记 |
| `backlog --store <resolved exact backlog root> stats --json` | 了解项目待办概览 | 每次新增/更新条目后自动反映，向用户汇报 |

## 项目速览

```
src/backlog/
├── models.py   # 数据模型：枚举(Priority/Status等)、BacklogItem(Pydantic)、评分公式
├── items.py    # 存储层：文件 I/O、CRUD、ID 生成、索引生成
└── cli.py      # CLI 层：Typer 8 个子命令、Rich 表格渲染
```

## 常用命令

```bash
# 开发环境
uv sync --group dev                                      # 安装依赖（包含 dev dependency group）
uv run backlog --help                                    # 查看命令帮助

# 代码质量
uv run --group dev python -m pytest                      # 测试
uv run --group dev ruff check .                          # Lint
uv run --group dev python -m pyright                     # 类型检查

# 手动测试（先解析 backlog-cli 并校验 backlog/store@1 descriptor）
/home/ling/workspace/workspace-control/bin/workspace project resolve backlog-cli --catalog /home/ling/workspace/workspace-control/catalog/workspace.json --json
uv run backlog --store <resolved-artifacts.backlog.root> list --status todo --json
uv run backlog --store <resolved-artifacts.backlog.root> add -T "测试" -c testing --priority P3 --json
uv run backlog --store <resolved-artifacts.backlog.root> stats --json
```
