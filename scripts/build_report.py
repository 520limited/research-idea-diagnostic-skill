#!/usr/bin/env python3
"""
build_report.py — 诊断报告导出器（Markdown → PDF，Typst 引擎，无浏览器内核）

为什么用 Typst
--------------
1. **纯 Python 调用**：`pip install typst` 自带编译引擎，不依赖 Chrome/Edge；
2. **排版质量接近 LaTeX**：真正的两端对齐、首行缩进、中文断行、自动目录、页码、
   页眉页脚、表格跨页重复表头、避免孤行寡行；
3. **比 WeasyPrint 省事**：WeasyPrint 依赖系统 GTK（libgobject 等），Windows 上
   常因缺 DLL 直接 import 失败；Typst 是静态链接的单一二进制。

对比结论（本项目实测）
----------------------
- WeasyPrint：Windows 缺 libgobject-2.0-0 → 不可用
- Chrome headless：可用，但需要浏览器内核，且 @page 页边距框不支持、页码要靠
  命令行 header/footer 模板 hack
- **Typst：可用，无外部依赖，排版能力最强 → 本文件采用**

用法
----
    python build_report.py --md report.md --pdf report.pdf
    python build_report.py --md report.md --pdf report.pdf --toc --title "选题诊断"
    python build_report.py --md report.md --typ out.typ      # 只生成中间 .typ，便于手改

依赖
----
    pip install typst

    Typst 会使用系统已安装字体。中文字体按以下优先级回退：
    Source Han Serif SC → Noto Serif SC → SimSun → Songti SC → serif
    若全部缺失会报错提示，而不是静默用错字体（如隶书 LiSu）。

返回码
------
    0 成功；2 缺少 typst；3 编译失败；4 输入缺失
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------- 中文字体探测

# 正文用衬线。注意：必须把「具备 400 字重」的字体排在前面，
# 否则 Typst 会匹配到只有 900 字重的 Source Han Serif SC，
# 或退化到 LiSu（隶书），正文会非常难看。
CJK_SERIF = [
    "SimSun",              # 400，Windows 标配，中文正文最稳
    "Noto Serif SC",       # 200/400/600（按系统安装情况）
    "Source Han Serif SC", # 常只有 900，不宜作正文首选
    "Songti SC",
    "STSong",
    "serif",
]
# 标题用无衬线。Microsoft YaHei 具备 290/400/700，最稳
CJK_SANS = [
    "Microsoft YaHei",
    "Noto Sans SC",
    "SimHei",
    "PingFang SC",
    "sans-serif",
]
# 等宽：代码里可能含中文注释，因此必须同时给出中文回退，
# 否则中文会落到 LiSu（隶书），代码注释会变得很怪。
MONO = [
    "Cascadia Mono",
    "Consolas",
    "JetBrains Mono",
    "SF Mono",
    "Microsoft YaHei",
    "SimSun",
    "monospace",
]


def available_families() -> set[str]:
    try:
        import typst  # noqa: PLC0415

        return set(typst.Fonts().families())
    except Exception:  # noqa: BLE001
        return set()


def pick_fonts() -> tuple[str, str, str]:
    """选择实际存在的中文字体，返回 Typst 字体元组字符串。"""
    fams = available_families()
    if not fams:
        # 无法探测时给一组安全的回退，由 Typst 自行处理
        return ('("Source Han Serif SC","Noto Serif SC","SimSun","serif")',
                '("Source Han Sans SC","Noto Sans SC","Microsoft YaHei","sans-serif")',
                '("Consolas","monospace")')

    def fmt(cands: list[str]) -> str:
        present = [c for c in cands if c in fams]
        if not present:  # 全都没有时保留候选，让 Typst 报清晰错误
            present = cands[:2]
        return "(" + ", ".join(f'"{c}"' for c in present) + ")"

    return fmt(CJK_SERIF), fmt(CJK_SANS), fmt(MONO)


# ---------------------------------------------------------------- 行内转换

def esc(text: str) -> str:
    """转义 Typst 标记字符（用于纯文本片段）。"""
    text = text.replace("\\", "\\\\")
    for ch in ("#", "$", "@", "*", "_", "`", "<", ">", "[", "]", "~"):
        text = text.replace(ch, "\\" + ch)
    return text


def _typst_string(s: str) -> str:
    """生成 Typst 字符串字面量（用于 raw() 的内容）。"""
    out = s.replace("\\", "\\\\").replace('"', '\\"')
    out = out.replace("\n", "\\n").replace("\r", "")
    return '"' + out + '"'


def inline(text: str) -> str:
    """Markdown 行内 → Typst 行内。

    关键点：所有生成的标记片段先放入占位符表，最后只对其余纯文本做转义。
    若先转换再整体转义（或反之），加粗/斜体会被二次处理，
    例如 **x** → *x* → 再走斜体规则 → _x_ → 转义 → 字面量，导致排版失效。
    """
    stash: dict[str, str] = {}

    def put(markup: str) -> str:
        key = f"\u0000{len(stash)}\u0000"
        stash[key] = markup
        return key

    # 1) 行内代码（先做，避免内部下划线被当斜体）
    text = re.sub(
        r"`([^`\n]+)`",
        lambda m: put("#raw(" + _typst_string(m.group(1)) + ")"),
        text,
    )

    # 2) 链接
    text = re.sub(
        r"\[([^\]]+)\]\(([^)\s]+)\)",
        lambda m: put(f'#link("{m.group(2)}")[{esc(m.group(1))}]'),
        text,
    )

    # 3) 加粗（必须在斜体之前）
    text = re.sub(
        r"\*\*([^*\n]+)\*\*",
        lambda m: put("*" + esc(m.group(1)) + "*"),
        text,
    )

    # 4) 斜体（只匹配单个星号，且不与单词粘连）
    text = re.sub(
        r"(?<![\w*\\])\*([^*\n]+)\*(?![\w*])",
        lambda m: put("_" + esc(m.group(1)) + "_"),
        text,
    )

    # 5) 其余纯文本转义，最后还原占位符
    parts = re.split(r"(\u0000\d+\u0000)", text)
    return "".join(stash.get(p, esc(p)) for p in parts)


# ---------------------------------------------------------------- 块级转换

def _split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def md_to_typst(md: str, *, toc: bool = False, drop_first_h1: bool = False) -> str:
    lines = md.splitlines()
    body: list[str] = []
    i = 0
    in_code = False
    code_buf: list[str] = []
    code_lang = ""
    list_depth = 0
    ordered_stack: list[int] = []
    dropped_h1 = False

    def close_lists() -> None:
        nonlocal list_depth
        while list_depth > 0:
            body.append("")
            list_depth -= 1
        ordered_stack.clear()

    while i < len(lines):
        line = lines[i]

        # ---- 代码块
        if line.strip().startswith("```"):
            if in_code:
                code = "\n".join(code_buf)
                # 注意：raw() 的 content 参数必须是字符串，不能是内容块。
                # 写成 #raw(block: true, lang: none, ```...```) 会报
                # "expected string, found content"。
                body.append(f"#raw(\n  {_typst_string(code)},\n  block: true,\n  lang: none,\n)")
                body.append("")
                code_buf, in_code, code_lang = [], False, ""
            else:
                in_code = True
                code_lang = line.strip()[3:].strip()
            i += 1
            continue
        if in_code:
            code_buf.append(line)
            i += 1
            continue

        # ---- 空行
        if not line.strip():
            close_lists()
            body.append("")
            i += 1
            continue

        # ---- 表格
        if "|" in line and i + 1 < len(lines) and re.match(r"^\s*\|?[\s:|-]+\|[\s:|-]*$", lines[i + 1]):
            close_lists()
            header = _split_row(line)
            i += 2
            rows: list[list[str]] = []
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(_split_row(lines[i]))
                i += 1
            ncol = len(header)
            # 首列窄、其余等分；列宽按内容长度粗略加权
            weights: list[float] = []
            for c in range(ncol):
                cells = [header[c]] + [r[c] if c < len(r) else "" for r in rows]
                avg = sum(len(x) for x in cells) / max(1, len(cells))
                weights.append(max(0.6, min(3.0, avg / 14)))
            total = sum(weights)
            cols = ", ".join(f"{w / total:.3f}fr" for w in weights)

            P = PALETTE
            body.append("#figure(")
            body.append("  table(")
            body.append(f"    columns: ({cols}),")
            body.append("    inset: (x: 5pt, y: 4.5pt),")
            body.append("    align: (left + top),")
            body.append(f'    stroke: 0.4pt + rgb("{P["table_rule"]}"),')
            body.append(
                "    fill: (_, y) => if y == 0 { "
                f'rgb("{P["secondary_soft"]}")'
                " } else { if calc.even(y) { "
                f'rgb("{P["zebra"]}")'
                " } else { white } },"
            )
            body.append("    table.header(")
            for c in header:
                body.append(f'      [*#text(fill: rgb("{P["secondary"]}"))[{inline(c)}]*],')
            body.append("    ),")
            for r in rows:
                padded = (r + [""] * ncol)[:ncol]
                for c in padded:
                    body.append(f"    [{inline(c)}],")
            body.append("  ),")
            body.append("  caption: none,")
            body.append(")")
            body.append("")
            continue

        # ---- 引用块
        if re.match(r"^\s*>", line):
            close_lists()
            q: list[str] = []
            while i < len(lines) and re.match(r"^\s*>", lines[i]):
                q.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            inner = " ".join(inline(x) for x in q if x.strip())
            if inner:
                P = PALETTE
                body.append("#block(")
                body.append("  width: 100%,")
                body.append("  inset: (x: 10pt, y: 7pt),")
                body.append("  radius: 2pt,")
                body.append(f'  fill: rgb("{P["primary_soft"]}"),')
                body.append(f'  stroke: (left: 3pt + rgb("{P["primary_line"]}")),')
                # 注意：block() 的位置参数才是内容；par(...) 不能作为参数传入，
                # 必须用 context/set 包在内容里，否则报 "missing argument: body"
                body.append("  {")
                body.append("    set par(first-line-indent: 0em, justify: true, leading: 0.8em)")
                body.append(f"    [{inner}]")
                body.append("  },")
                body.append(")")
                body.append("")
            continue

        # ---- 标题
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            close_lists()
            level, text = len(m.group(1)), m.group(2).strip()
            if level == 1 and drop_first_h1 and not dropped_h1:
                dropped_h1 = True   # 已升到封面，正文不再重复
                i += 1
                continue
            marker = "=" * level
            body.append(f"{marker} {inline(text)}")
            body.append("")
            i += 1
            continue

        # ---- 水平线
        if re.match(r"^\s*---+\s*$", line):
            close_lists()
            body.append("#v(4pt)\n#line(length: 100%, stroke: 0.4pt + rgb(\"#d8d8d8\"))\n#v(4pt)")
            i += 1
            continue

        # ---- 列表
        m_ul = re.match(r"^(\s*)[-*+]\s+(.*)$", line)
        m_ol = re.match(r"^(\s*)\d+[.)]\s+(.*)$", line)
        if m_ul or m_ol:
            indent = len((m_ul or m_ol).group(1))
            content = (m_ul or m_ol).group(2)
            depth = 1 if indent < 2 else 2
            while list_depth < depth and list_depth < 2:
                body.append("")
                list_depth += 1
            marker = "-" if m_ul else "+"
            body.append(f"{marker} {inline(content)}")
            i += 1
            continue

        # ---- 普通段落
        close_lists()
        body.append(inline(line))
        body.append("")
        i += 1

    if in_code and code_buf:
        body.append(f"#raw(\n  {_typst_string(chr(10).join(code_buf))},\n  block: true,\n  lang: none,\n)")

    return "\n".join(body)


def build_typst(md: str, *, title: str, meta: str | None, toc: bool, cover: bool) -> str:
    serif, sans, mono = pick_fonts()
    P = PALETTE

    # 若正文以一级标题开头，用它作为封面标题并在正文中移除，避免标题出现两次
    md = md.lstrip("\n")
    m_h1 = re.match(r"^#\s+(.+?)\s*$", md.split("\n", 1)[0] if md else "")
    title_from_body = False
    if m_h1:
        title = m_h1.group(1).strip() or title
        title_from_body = True
        rest = md.split("\n", 1)[1] if "\n" in md else ""
        md = rest.lstrip("\n")

    # 有封面时，正文不再重复一级标题；无封面时保留
    drop_first_h1 = cover or title_from_body

    cover_block = ""
    if cover:
        meta_line = (
            f"#v(7pt)\n#text(font: {sans}, fill: rgb(\"{P['ink_faint']}\"), size: 9pt)[{esc(meta)}]"
            if meta
            else ""
        )
        cover_block = f"""
#align(center)[
  #v(14pt)
  #text(font: {sans}, size: 8pt, tracking: 3pt, fill: rgb("{P['primary_line']}"))[RESEARCH IDEA DIAGNOSTIC]
  #v(10pt)
  #text(font: {sans}, size: 20pt, weight: 700, fill: rgb("{P['primary']}"))[{esc(title)}]
  {meta_line}
  #v(12pt)
  #line(length: 100%, stroke: 1pt + rgb("{P['primary_line']}"))
]
#v(16pt)
"""

    toc_block = ""
    if toc:
        # Typst 0.15 的 outline.entry 已改为 content-based，不再暴露 body/page/fill 字段，
        # 因此不再自定义 entry（自定义会 panic）。默认 outline 自带点导线与页码，样式已够用，
        # 只对标题与颜色做定制。
        toc_tpl = """
#outline(
  title: [#text(font: __SANS__, size: 15pt, weight: 700, fill: rgb("__PRIMARY__"))[目录]],
  indent: auto,
  depth: 2,
)
#pagebreak()
"""
        toc_block = toc_tpl.replace("__SANS__", sans).replace("__PRIMARY__", P["primary"])

    config = f"""
#set document(title: {repr_typst(title)})
#set page(
  paper: "a4",
  margin: (x: 17mm, y: 19mm),
  numbering: "1 / 1",
  number-align: center,
)
#set text(font: {serif}, size: 10.5pt, lang: "zh", fill: rgb("{P['ink']}"))
#set par(
  justify: true,
  leading: 0.82em,
  spacing: 1.12em,
  first-line-indent: (amount: 2em, all: true),
)
#set heading(numbering: none)
// 标题不参与首行缩进（否则标题文字会被推右 2em）
#show heading: set par(first-line-indent: 0em, justify: false)

// 页码颜色
#set page(numbering: "1 / 1", number-align: center)
#show page.where(numbering: it => true): it => it

// 一级标题：星辰紫 + 渐细下划线
#show heading.where(level: 1): it => {{
  v(16pt)
  block(width: 100%,
    text(font: {sans}, size: 15pt, weight: 700, fill: rgb("{P['primary']}"), it.body))
  v(2pt)
  line(length: 100%, stroke: 1.4pt + rgb("{P['primary']}"))
  v(7pt)
}}
// 二级标题：深蓝 + 左侧色条
#show heading.where(level: 2): it => {{
  v(12pt)
  block(width: 100%,
    stack(dir: ltr, spacing: 8pt,
      line(angle: 90deg, length: 0.95em, stroke: 2.6pt + rgb("{P['primary_line']}")),
      pad(left: 0pt, text(font: {sans}, size: 12pt, weight: 700, fill: rgb("{P['secondary']}"), it.body)),
    ))
  v(4pt)
}}
// 三级标题
#show heading.where(level: 3): it => {{
  v(9pt)
  text(font: {sans}, size: 10.8pt, weight: 700, fill: rgb("{P['ink_soft']}"), it.body)
  v(3pt)
}}
// 四级及以下
#show heading.where(level: 4): it => {{
  v(7pt)
  text(font: {sans}, size: 10.2pt, weight: 600, fill: rgb("{P['ink_muted']}"), it.body)
  v(2pt)
}}

#set strong(delta: 130)
#show strong: set text(fill: rgb("{P['primary']}"))
#show link: underline
#show link: set text(fill: rgb("{P['accent']}"))

// 表格：深蓝表头、斑马纹、细边框；强制铺满正文宽度，避免溢出页边距
#show table: set text(size: 9pt, font: {sans}, fill: rgb("{P['ink']}"))
#show table: set par(first-line-indent: 0em, justify: false, leading: 0.62em)
#show table: it => block(width: 100%, it)

// 代码块
#show raw.where(block: true): it => {{
  block(
    width: 100%,
    inset: (x: 9pt, y: 7pt),
    radius: 3pt,
    fill: rgb("{P['code_bg']}"),
    stroke: 0.5pt + rgb("{P['code_rule']}"),
    text(font: {mono}, size: 8pt, fill: rgb("{P['ink_soft']}"), it),
  )
}}
// 行内代码
#show raw.where(block: false): it => {{
  box(inset: (x: 3pt, y: 1.2pt), radius: 2pt, fill: rgb("{P['code_inline']}"),
      text(font: {mono}, size: 8.8pt, fill: rgb("{P['secondary']}"), it))
}}

// 列表：不首行缩进
#show list: set par(first-line-indent: 0em)
#show enum: set par(first-line-indent: 0em)
"""

    body = md_to_typst(md, toc=toc, drop_first_h1=drop_first_h1)
    return f"{config}\n{cover_block}\n{toc_block}\n{body}\n"


def repr_typst(s: str) -> str:
    """把 Python 字符串转成 Typst 字符串字面量。"""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


# ---------------------------------------------------------------- 配色（星辰紫 · 深蓝）

# 修改配色只改这一个字典即可
PALETTE = {
    # 主色：星辰紫
    "primary":      "#4C3A8C",
    "primary_soft": "#EDEAF7",   # 引用块底色
    "primary_line": "#6B5BB5",   # 分隔线、次级强调
    # 辅色：深蓝
    "secondary":    "#1E3A6E",
    "secondary_soft": "#E8EDF6",  # 表头底色
    # 强调：星尘蓝（数据、关键数字）
    "accent":       "#3B6FB6",
    # 中性
    "ink":          "#1C1C22",   # 正文
    "ink_soft":     "#3A3A44",   # 二级标题
    "ink_muted":    "#5C5C68",   # 三级标题
    "ink_faint":    "#8A8A96",   # 元信息
    "rule":         "#C9C9D4",
    "table_rule":   "#CFCFDA",
    "zebra":        "#F7F7FB",
    "code_bg":      "#F4F4F8",
    "code_rule":    "#DEDEE8",
    "code_inline":  "#EFEFF5",
}


# ---------------------------------------------------------------- 编译

def compile_pdf(typ_source: str, out_pdf: Path, workdir: Path) -> bool:
    try:
        import typst  # noqa: PLC0415
    except ImportError:
        print("! 缺少 typst。请运行：pip install typst", file=sys.stderr)
        return False

    workdir.mkdir(parents=True, exist_ok=True)
    typ_path = workdir / "_report.typ"
    typ_path.write_text(typ_source, encoding="utf-8")

    try:
        # 注意：必须传文件路径而非源码字符串（源码字符串会触发路径 canonicalize 错误）
        pdf_bytes = typst.compile(str(typ_path), root=str(workdir))
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        print(f"! Typst 编译失败：\n{msg[:3000]}", file=sys.stderr)
        if "unknown font family" in msg.lower() or "font" in msg.lower():
            print(
                "\n提示：可能是中文字体缺失。当前可用字体：\n  "
                + ", ".join(sorted(available_families())[:40]),
                file=sys.stderr,
            )
        return False

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    out_pdf.write_bytes(pdf_bytes)
    return True


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

    ap = argparse.ArgumentParser(description="诊断报告导出（Markdown → PDF，Typst 引擎）")
    ap.add_argument("--md", type=Path, required=True, help="输入 Markdown")
    ap.add_argument("--pdf", type=Path, help="输出 PDF")
    ap.add_argument("--typ", type=Path, help="同时保存中间 .typ 源文件")
    ap.add_argument("--title", default="选题诊断报告")
    ap.add_argument("--meta", default=None, help="封面元信息")
    ap.add_argument("--toc", action="store_true", help="生成目录（Typst outline）")
    ap.add_argument("--no-cover", action="store_true", help="不生成封面")
    args = ap.parse_args()

    if not args.md.exists():
        print(f"! 输入不存在: {args.md}", file=sys.stderr)
        return 4

    md = args.md.read_text(encoding="utf-8")
    src = build_typst(md, title=args.title, meta=args.meta, toc=args.toc, cover=not args.no_cover)

    if args.typ:
        args.typ.parent.mkdir(parents=True, exist_ok=True)
        args.typ.write_text(src, encoding="utf-8")
        print(f"Typst 源已写入 {args.typ}")

    if not args.pdf:
        if not args.typ:
            print("! 需要 --pdf 或 --typ 至少之一", file=sys.stderr)
            return 4
        return 0

    # 工作目录放在 PDF 同级的隐藏目录，避免中文路径问题（Typst 支持，但缩短更稳）
    workdir = args.pdf.parent / ".typst_build"
    if compile_pdf(src, args.pdf.resolve(), workdir):
        size = args.pdf.stat().st_size
        print(f"PDF 已生成: {args.pdf}  ({size:,} bytes)")
        return 0
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
