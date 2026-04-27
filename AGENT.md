# AGENT.md — Backlog CLI 开发路由中枢

> 人类开发者请阅读 [README.md](README.md)。外部 AI 如需**使用** backlog 工具，系统 skill `backlog` 已自动装载合约内容，也可阅读 [agent/AGENT_CONTRACT.md](agent/AGENT_CONTRACT.md)。

## 绝对红线

- 语言：Python 3.12+
- 依赖最小化：Typer (CLI) + Pydantic (模型) + python-frontmatter (序列化) + Rich (输出) + PyYAML
- 每层只做一件事：models (数据) → items (存储) → cli (展示)
- 文件系统存储，不引入数据库
- 变更后运行 `uv run ruff check . && uv run pyright` 确保通过

## 路由表

| 文档 | 何时读 | 何时更新 |
|------|--------|----------|
| [agent/AGENT_CONTRACT.md](agent/AGENT_CONTRACT.md) | 外部 AI 学习如何使用 backlog 工具 | 新增/修改 CLI 命令、数据字段、排序规则后 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 修改模块结构、引入新依赖、改变存储方案 | 组件关系变更、新增模块、技术选型变化 |
| `backlog list --status todo` | 寻找下一个开发任务 | 完成条目后通过 `backlog update --fixed` 标记 |
| `backlog stats` | 了解项目待办概览 | 每次新增/更新条目后自动反映 |

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
uv sync                                                  # 安装依赖
uv run backlog --help                                    # 查看命令帮助

# 代码质量
uv run ruff check .                                      # Lint
uv run pyright                                           # 类型检查

# 手动测试（以当前项目自身为靶场）
uv run backlog --dir . list --status todo
uv run backlog --dir . add -p backlog-cli -t "测试" -c testing --priority P3
uv run backlog --dir . stats
```
