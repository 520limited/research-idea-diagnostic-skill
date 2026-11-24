#!/usr/bin/env python3
"""
build_onepager.py — 单页速览导出（Typst）

与 build_report.py 的区别
-------------------------
- build_report.py 用于 10+ 页的完整诊断报告（封面 + 目录 + 12 节）
- **本脚本用于一页纸速览**：更小字号、更紧行距、无封面无目录，
  目标是"扫一眼就能决策"

设计原则
--------
一页纸的信息密度靠三件事，不是靠删内容：
1. 字号 9pt、行距 0.62em（正文仍可读）
2. 标题只保留两级，且用左侧色条压缩垂直空间
3. 表格去掉内边距与斑马纹，只留横线

用法
----
    python build_onepager.py --md onepager.md --pdf onepager.pdf
    python build_onepager.py --md onepager.md --pdf out.pdf --title "诊断速览"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 复用主脚本的解析与配色，避免两套逻辑漂移
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_report import (  # noqa: E402
    PALETTE,
    esc,
    inline,
    md_to_typst,
    pick_fonts,
    repr_typst,
)


def build_onepager_typst(md: str, *, title: str, meta: str | None) -> str:
    serif, sans, mono = pick_fonts()
    P = PALETTE

    # 正文首个 h1 提为页眉标题
    md = md.lstrip("\n")
    first_line = md.split("\n", 1)[0] if md else ""
    if first_line.startswith("# "):
        title = first_line[2:].strip() or title
        md = md.split("\n", 1)[1] if "\n" in md else ""
        md = md.lstrip("\n")

    meta_html = (
        f'#line(length: 100%, stroke: 0.5pt + rgb("{P["rule"]}"))\n'
        f'#v(2pt)\n'
        f'#text(font: {sans}, size: 7.6pt, fill: rgb("{P["ink_faint"]}"))[{esc(meta)}]'
        if meta
        else ""
    )

    config = f"""
#set document(title: {repr_typst(title)})
#set page(
  paper: "a4",
  margin: (x: 13mm, y: 11mm),
  numbering: "1 / 1",
  number-align: center,
)
#set text(font: {serif}, size: 9.6pt, lang: "zh", fill: rgb("{P['ink']}"))
#set par(
  justify: true,
  leading: 0.68em,
  spacing: 0.74em,
  first-line-indent: (amount: 0em, all: false),
)

// 一级标题：一条色带，不加额外垂直空白
#show heading.where(level: 1): it => {{
  v(5pt)
  block(width: 100%,
    text(font: {sans}, size: 10.4pt, weight: 700, fill: rgb("{P['primary']}"), it.body))
  v(1.2pt)
  line(length: 100%, stroke: 0.7pt + rgb("{P['primary']}"))
  v(2.4pt)
}}
// 二级标题：左侧短色条
#show heading.where(level: 2): it => {{
  v(4pt)
  stack(dir: ltr, spacing: 5pt,
    line(angle: 90deg, length: 0.8em, stroke: 2pt + rgb("{P['primary_line']}")),
    text(font: {sans}, size: 9.9pt, weight: 700, fill: rgb("{P['secondary']}"), it.body),
  )
  v(2pt)
}}
#show heading.where(level: 3): it => {{
  v(2.5pt)
  text(font: {sans}, size: 8.8pt, weight: 700, fill: rgb("{P['ink_soft']}"), it.body)
  v(1pt)
}}
#show heading: set par(first-line-indent: 0em, justify: false)

#set strong(delta: 130)
#show strong: set text(fill: rgb("{P['primary']}"))
#show link: underline
#show link: set text(fill: rgb("{P['accent']}"))

// 表格：紧凑横线风格，适合一页纸
#show table: set text(size: 8.6pt, font: {sans}, fill: rgb("{P['ink']}"))
#show table: set par(first-line-indent: 0em, justify: false, leading: 0.5em)
#show table: it => block(width: 100%, it)

#show raw.where(block: true): it => {{
  block(width: 100%, inset: (x: 7pt, y: 5pt), radius: 2pt,
    fill: rgb("{P['code_bg']}"), stroke: 0.4pt + rgb("{P['code_rule']}"),
    text(font: {mono}, size: 7.2pt, it))
}}
#show raw.where(block: false): it => {{
  box(inset: (x: 2.4pt, y: 0.8pt), radius: 1.5pt, fill: rgb("{P['code_inline']}"),
      text(font: {mono}, size: 7.8pt, fill: rgb("{P['secondary']}"), it))
}}
#show list: set par(first-line-indent: 0em)
#show enum: set par(first-line-indent: 0em)
"""

    header = f"""
#align(left)[
  #text(font: {sans}, size: 7.2pt, tracking: 2.4pt, fill: rgb("{P['primary_line']}"))[RESEARCH IDEA DIAGNOSTIC]
  #v(3pt)
  #text(font: {sans}, size: 16pt, weight: 700, fill: rgb("{P['primary']}"))[{esc(title)}]
  #v(3pt)
  {meta_html}
]
#v(4pt)
"""

    body = md_to_typst(md, toc=False, drop_first_h1=False)
    return f"{config}\n{header}\n{body}\n"


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

    ap = argparse.ArgumentParser(description="单页速览导出（Typst）")
    ap.add_argument("--md", type=Path, required=True)
    ap.add_argument("--pdf", type=Path, required=True)
    ap.add_argument("--title", default="选题诊断速览")
    ap.add_argument("--meta", default=None)
    args = ap.parse_args()

    if not args.md.exists():
        print(f"! 输入不存在: {args.md}", file=sys.stderr)
        return 4

    md = args.md.read_text(encoding="utf-8")
    src = build_onepager_typst(md, title=args.title, meta=args.meta)

    # 检查页数，超一页给出明确提示（便于作者继续精简）
    try:
        import typst  # noqa: PLC0415

        workdir = args.pdf.parent / ".typst_build"
        workdir.mkdir(parents=True, exist_ok=True)
        typ_path = workdir / "_onepager.typ"
        typ_path.write_text(src, encoding="utf-8")
        pdf_bytes = typst.compile(str(typ_path), root=str(workdir))
    except ImportError:
        print("! 缺少 typst。请运行：pip install typst", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"! 编译失败：\n{str(exc)[:2500]}", file=sys.stderr)
        return 3

    args.pdf.parent.mkdir(parents=True, exist_ok=True)
    args.pdf.write_bytes(pdf_bytes)

    # 统计页数
    page_count = pdf_bytes.count(b"/Type /Page") - pdf_bytes.count(b"/Type /Pages")
    status = "✓ 正好一页" if page_count <= 1 else f"⚠ 共 {page_count} 页，建议精简"
    print(f"PDF 已生成: {args.pdf}  ({len(pdf_bytes):,} bytes)  {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

