"""Static checks for the durable public documentation contract."""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LINK_RE = re.compile(r"\[[^]]+\]\(([^)]+)\)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
EXPLICIT_ANCHOR_RE = re.compile(r"<a\s+id=[\"']([^\"']+)[\"']\s*></a>")


def _headings(markdown: str) -> list[tuple[int, str, int]]:
    headings: list[tuple[int, str, int]] = []
    in_fence = False
    offset = 0
    for line in markdown.splitlines(keepends=True):
        if line.strip().startswith("```"):
            in_fence = not in_fence
        elif not in_fence and (match := HEADING_RE.match(line)):
            headings.append((len(match.group(1)), match.group(2), offset))
        offset += len(line)
    return headings


def _section(markdown: str, heading: str) -> str:
    headings = _headings(markdown)
    start = next((index for index, (level, title, _) in enumerate(headings) if level == 2 and title == heading), None)
    assert start is not None, f"missing section: {heading}"
    _, _, start_offset = headings[start]
    end_offset = next((offset for level, _, offset in headings[start + 1 :] if level <= 2), len(markdown))
    return markdown[start_offset:end_offset]


def _assert_relative_links_resolve(path: Path) -> None:
    markdown = path.read_text(encoding="utf-8")
    for link in LINK_RE.findall(markdown):
        if ":" in link or link.startswith("#"):
            continue
        target_name, separator, anchor = link.partition("#")
        target = (path.parent / target_name).resolve()
        assert target.is_file(), f"broken link in {path}: {link}"
        if separator:
            assert anchor in target.read_text(encoding="utf-8"), f"broken anchor in {path}: {link}"


def test_primary_skill_keeps_an_exact_five_operation_fast_path() -> None:
    skill = (REPO_ROOT / "skills/backlog/SKILL.md").read_text(encoding="utf-8")
    fast_path = _section(skill, "Agent 官方快速路径")
    reference = _section(skill, "按需参考：人类与管理员")

    assert "Host 先通过 Catalog resolver 取得 exact `backlog/store@1` root" in fast_path
    assert "backlog --store <absolute-backlog-root> <子命令> --json" in fast_path
    operations = set(re.findall(r"\| [^|]+ \| `([a-z-]+)(?:\s|`)", fast_path))
    assert operations == {"list", "show", "add", "update", "next"}

    for compatibility_term in (
        "validate-store",
        "stats",
        "index",
        "edit",
        "--fixed",
        "Rich",
        "CSV",
        "effort",
        "impact",
        "score",
        "extra",
        "tags",
        "source",
        "related_docs",
        "人类输出",
        "Rich table",
        "非 JSON",
    ):
        assert compatibility_term not in fast_path
        assert compatibility_term in reference

    for removed_surface in ("--target", "provision-store", "CWD", "legacy"):
        assert removed_surface not in skill


def test_responsibility_sections_keep_host_workflow_and_body_judgment_separate() -> None:
    skill = (REPO_ROOT / "skills/backlog/SKILL.md").read_text(encoding="utf-8")
    architecture = (REPO_ROOT / "docs/ARCHITECTURE.md").read_text(encoding="utf-8")
    body_contract = _section(skill, "Agent 责任：正文完整性")
    responsibility = _section(architecture, "宿主与工作流责任边界")

    assert "CLI 只保存正文，不机械判定其是否可执行" in body_contract
    assert "Workspace Control 是本机宿主" in responsibility
    assert "Catalog resolved context 找到 `backlog/store@1` exact root" in responsibility
    assert "Sigil 是执行工作流" in responsibility
    assert "完成判断" in responsibility


def test_architecture_mermaid_has_one_exact_context_construction_path() -> None:
    architecture = (REPO_ROOT / "docs/ARCHITECTURE.md").read_text(encoding="utf-8")
    overview = _section(architecture, "架构概览")

    assert "图意：箭头表示调用与数据流" in overview
    assert "Exact[exact `--store` entry]" in overview
    assert "Exact --> StrictLoad[strict `load_store()`]" in overview
    assert "StrictLoad --> Context[StoreContext]" in overview
    assert "Context --> Items[items.py - exact core CRUD & I/O]" in overview
    for removed_surface in ("Legacy", "legacy", "--target", "CWD", "compatibility temporary context"):
        assert removed_surface not in overview


def test_document_links_preserve_the_single_skill_authority() -> None:
    for path in (REPO_ROOT / "README.md", REPO_ROOT / "skills/backlog/SKILL.md"):
        _assert_relative_links_resolve(path)


def test_primary_skill_preserves_the_unique_explicit_body_contract_anchor() -> None:
    skill = (REPO_ROOT / "skills/backlog/SKILL.md").read_text(encoding="utf-8")

    assert EXPLICIT_ANCHOR_RE.findall(skill) == ["Backlog-Item-Body-Contract"]
