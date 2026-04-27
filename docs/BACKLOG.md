# BACKLOG.md — Backlog CLI 需求池

## CLI & UX

- [ ] `backlog list` 支持 `--format json|table|csv` 统一输出格式
- [ ] 颜色输出可配置（`--no-color`）

## 数据与存储

- [ ] 条目支持自定义字段（`extra: {}` 字典）
- [ ] `depends_on` 自动校验目标条目是否存在
- [ ] 被依赖条目未完成时自动标记为 blocked

## 工程与质量

- [ ] 补充单元测试（models.py → items.py → cli.py）
- [ ] CI 配置（GitHub Actions: ruff + pyright + pytest）
