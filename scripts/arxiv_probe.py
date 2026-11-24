#!/usr/bin/env python3
"""
arxiv_probe.py — 新颖性检索探针（仅标准库）

设计原则
--------
1. 检索的目的是**杀死自己的想法**，不是找支持文献。因此默认输出包含
   "重叠度判定" 字段，而不是只列标题。
2. arXiv API 对无 API key 的调用有速率限制，实测每 3 秒一次安全，
   连续 3 次以上容易 429。脚本内建串行限速与指数退避。
3. 一个概念至少要查 3 种英文说法。脚本提供 SYNONYM_BANK，
   用户按自己的概念改写后查询。

用法
----
    python arxiv_probe.py --preset memory_pe
    python arxiv_probe.py --query 'all:"SAM 2" AND all:"memory"' --query 'all:"positional encoding" AND all:medical'
    python arxiv_probe.py --preset memory_pe --out probe.md --limit 30

输出
----
    终端表格 + 可选 Markdown 文件。每行给出：日期 | 编号 | 标题 | 主要作者
    以及一个提示性的 "REVIEW" 级别标记（基于关键词共现的粗略判定，
    最终判定必须人工复核）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

API = "https://export.arxiv.org/api/query"
NS = {"a": "http://www.w3.org/2005/Atom"}

# 用户可自行扩充：把左侧概念换成自己的，右侧列 3+ 种英文说法
SYNONYM_BANK: dict[str, list[str]] = {
    "memory_pe": [
        'all:"SAM 2" AND all:"positional encoding"',
        'all:"memory bank" AND all:"positional encoding"',
        'all:"temporal position" AND all:"memory attention"',
    ],
    "anisotropic": [
        'all:"anisotropic" AND all:"slice spacing"',
        'all:"voxel spacing" AND all:"segmentation" AND all:"foundation model"',
        'all:"slice thickness" AND all:"memory"',
    ],
    "cross_plane": [
        'all:"coronal" AND all:"sagittal" AND all:"memory bank"',
        'all:"multi-view" AND all:"SAM 2"',
        'all:"cross-plane" AND all:"segmentation"',
    ],
    "prompt_efficiency": [
        'all:"annotation efficiency" AND all:"SAM"',
        'all:"prompt budget" AND all:"segmentation"',
        'all:"interactive" AND all:"volumetric" AND all:"propagation"',
    ],
}

# 用于粗略标注"可能高度重叠"的关键词；命中越多越要人工复核
OVERLAP_HINTS = [
    "memory", "positional", "encoding", "anisotrop", "spacing",
    "coronal", "sagittal", "cross-plane", "prompt", "slice",
]


def _fetch(query: str, limit: int, start: int, timeout: int = 40) -> ET.Element:
    params = {
        "search_query": query,
        "start": str(start),
        "max_results": str(limit),
        "sortBy": "relevance",
        "sortOrder": "descending",
    }
    url = f"{API}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "research-idea-diagnostic/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return ET.fromstring(raw)


def _parse(feed: ET.Element) -> list[dict]:
    out = []
    for e in feed.findall("a:entry", NS):
        title = re.sub(r"\s+", " ", (e.findtext("a:title", "", NS) or "")).strip()
        published = (e.findtext("a:published", "", NS) or "")[:10]
        entry_id = e.findtext("a:id", "", NS) or ""
        bare_id = entry_id.replace("http://arxiv.org/abs/", "").replace("http://arxiv.org/abs/", "")
        summary = re.sub(r"\s+", " ", (e.findtext("a:summary", "", NS) or "")).strip()
        authors = [
            (a.findtext("a:name", "", NS) or "").strip()
            for a in e.findall("a:author", NS)
        ]
        comment = (e.findtext("{http://arxiv.org/schemas/atom}comment", "", NS) or "").strip()
        out.append(
            {
                "id": bare_id,
                "title": title,
                "published": published,
                "authors": authors,
                "summary": summary,
                "comment": comment,
            }
        )
    return out


def probe(queries: list[str], limit: int, gap: float = 3.0, retries: int = 4) -> list[dict]:
    """串行查询，带指数退避。429/超时都按退避处理。"""
    results: list[dict] = []
    seen: set[str] = set()
    for qi, q in enumerate(queries, 1):
        print(f"[{qi}/{len(queries)}] {q}", file=sys.stderr)
        for attempt in range(retries):
            try:
                feed = _fetch(q, limit, 0)
                items = _parse(feed)
                new = 0
                for it in items:
                    if it["id"] in seen:
                        continue
                    seen.add(it["id"])
                    hits = sum(1 for h in OVERLAP_HINTS if h in (it["title"] + " " + it["summary"]).lower())
                    it["overlap_hint"] = "HIGH" if hits >= 4 else ("MID" if hits >= 2 else "LOW")
                    it["query"] = q
                    results.append(it)
                    new += 1
                print(f"    -> {len(items)} 条，去重后新增 {new}", file=sys.stderr)
                break
            except Exception as exc:  # noqa: BLE001 - 网络层异常种类多，统一退避
                wait = min(60, 5 * (2 ** attempt))
                print(f"    ! {type(exc).__name__}: {exc} — {wait}s 后重试", file=sys.stderr)
                if attempt == retries - 1:
                    print(f"    x 放弃该查询", file=sys.stderr)
                else:
                    time.sleep(wait)
        time.sleep(gap)  # 限速
    return results


def to_markdown(results: list[dict], queries: list[str]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# 新颖性检索原始记录",
        "",
        f"- 生成时间：{now}",
        f"- 查询数：{len(queries)}",
        f"- 去重后条目：{len(results)}",
        "",
        "> 说明：`重叠度` 列由关键词共现粗略估计（HIGH/MID/LOW），"
        "**仅用于排序，不能代替人工判定**。每条都必须读摘要后重新判定。",
        "",
        "## 查询式",
        "",
    ]
    for q in queries:
        lines.append(f"- `{q}`")
    lines += [
        "",
        "## 结果",
        "",
        "| 重叠度 | 日期 | arXiv ID | 标题 | 备注 |",
        "|---|---|---|---|---|",
    ]
    for it in sorted(results, key=lambda x: (x["overlap_hint"] != "HIGH", x["published"]), reverse=False):
        title = it["title"].replace("|", "\\|")
        comment = (it["comment"] or "").replace("|", "\\|")
        lines.append(
            f"| {it['overlap_hint']} | {it['published']} | [{it['id']}](https://arxiv.org/abs/{it['id']}) | {title} | {comment} |"
        )
    lines += [
        "",
        "## 判定栏（人工填写）",
        "",
        "| arXiv ID | 与本命题的重叠 | 判定（终止/降级/安全） | 处理方式 |",
        "|---|---|---|---|",
    ]
    for it in results:
        lines.append(f"| {it['id']} |  |  |  |")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="arXiv 新颖性检索探针（带限速）")
    ap.add_argument("--query", action="append", default=[], help="完整 arXiv search_query，可多次")
    ap.add_argument("--preset", action="append", default=[], choices=sorted(SYNONYM_BANK), help="预设词组")
    ap.add_argument("--limit", type=int, default=20, help="每个查询取多少条（默认 20）")
    ap.add_argument("--gap", type=float, default=3.0, help="查询间隔秒数（默认 3.0，低于 2 容易 429）")
    ap.add_argument("--out", type=Path, help="输出 Markdown 路径")
    ap.add_argument("--json", type=Path, help="可选：同时导出原始 JSON")
    args = ap.parse_args()

    queries = list(args.query)
    for p in args.preset:
        queries.extend(SYNONYM_BANK[p])
    if not queries:
        ap.error("至少提供一个 --query 或 --preset")

    results = probe(queries, args.limit, args.gap)

    md = to_markdown(results, queries)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(md, encoding="utf-8")
        print(f"\n已写入 {args.out}", file=sys.stderr)
    else:
        print(md)

    if args.json:
        args.json.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写入 {args.json}", file=sys.stderr)

    high = sum(1 for r in results if r["overlap_hint"] == "HIGH")
    print(
        f"\n合计 {len(results)} 条，其中 HIGH 重叠候选 {high} 条 —— 请逐条读摘要后判定。",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
