#!/usr/bin/env python3
"""
B站 danmaku collection and analysis script.

Usage:
  # Fetch danmaku
  python3 scripts/collect_danmaku.py fetch --bvid BVxxx --out-dir <path>
  python3 scripts/collect_danmaku.py fetch --cid 38352717132 --out-dir <path>

  # Analyze already-fetched danmaku
  python3 scripts/collect_danmaku.py analyze --input <danmaku.json> --output <report.md>
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import urllib.request
import zlib
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ── bvid / aid / cid conversion ──────────────────────────────────────────

TABLE = "fZodR9XQDSUm21yCkr6zBqiveYah8bt4xsWpHnJE7jL5VG3guMTKNPAwcF"
TR = {TABLE[i]: i for i in range(58)}
_S = [11, 10, 3, 8, 4, 6]
_XOR = 177451812
_ADD = 8728348608


def bvid_to_aid(bvid: str) -> int:
    r = sum(TR[bvid[_S[i]]] * (58 ** i) for i in range(6))
    return (r - _ADD) ^ _XOR


# ── API helpers ──────────────────────────────────────────────────────────

def get_video_info(bvid: str) -> dict[str, Any]:
    """Fetch video metadata. Returns {aid, cid, title, duration, pages}."""
    url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    d = data.get("data", {})
    return {
        "aid": d.get("aid"),
        "cid": d.get("cid"),
        "title": d.get("title"),
        "duration_s": d.get("duration"),
        "pages": [
            {"page": p.get("page"), "cid": p.get("cid"),
             "part": p.get("part"), "duration_s": p.get("duration")}
            for p in (d.get("pages") or [])
        ],
    }


def fetch_danmaku_xml(cid: int) -> list[dict[str, Any]]:
    """Fetch danmaku for a video cid using the XML endpoint."""
    url = f"https://api.bilibili.com/x/v1/dm/list.so?oid={cid}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": f"https://www.bilibili.com/video/av{cid}",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()

    # Decompress deflate
    data = zlib.decompress(raw, -zlib.MAX_WBITS)
    text = data.decode("utf-8", errors="replace")

    dms: list[dict[str, Any]] = []
    for match in re.finditer(r'<d p="([^"]*)">(.*?)</d>', text):
        attrs = match.group(1).split(",")
        dms.append({
            "time_s": float(attrs[0]) if attrs else 0,
            "type": int(attrs[1]) if len(attrs) > 1 else 0,
            "size": int(attrs[2]) if len(attrs) > 2 else 25,
            "color": int(attrs[3]) if len(attrs) > 3 else 0,
            "ctime": int(attrs[4]) if len(attrs) > 4 else 0,
            "pool": int(attrs[5]) if len(attrs) > 5 else 0,
            "content": match.group(2),
        })
    return dms


# ── Analysis ─────────────────────────────────────────────────────────────

def analyze_danmaku(
    danmaku: list[dict[str, Any]],
    *,
    title: str = "",
    bucket_s: int = 10,
    peak_n: int = 5,
    peak_method: str = "topn",
    filter_pool1: bool = True,
) -> dict[str, Any]:
    """Analyze danmaku: density peaks, keywords, themes."""

    # Filter subtitle danmaku
    if filter_pool1:
        dms = [d for d in danmaku if d.get("pool", 0) != 1]
    else:
        dms = list(danmaku)

    if not dms:
        return {"error": "no danmaku after filtering", "total": 0, "peaks": []}

    # Calculate basic stats
    duration = max(d.get("time_s", 0) for d in dms)
    density_per_min = len(dms) / max(duration / 60, 0.1)

    # Time bucket segmentation
    buckets: Counter = Counter()
    bucket_content: dict[int, list[str]] = defaultdict(list)
    for d in dms:
        key = int(d["time_s"] // bucket_s) * bucket_s
        buckets[key] += 1
        bucket_content[key].append(d["content"])

    # Find peaks
    if peak_method == "zscore" and len(buckets) >= 5:
        counts = list(buckets.values())
        mean_c = statistics.mean(counts)
        stdev_c = statistics.stdev(counts)
        peaks_raw = [(ts, cnt) for ts, cnt in buckets.items()
                     if (cnt - mean_c) / max(stdev_c, 0.01) > 1.5]
        peaks = sorted(peaks_raw, key=lambda x: -x[1])[:peak_n]
    else:
        peaks = buckets.most_common(peak_n)

    # For each peak: keywords + sample quotes
    peak_details = []
    for ts, count in peaks:
        contents = bucket_content[ts]
        minutes = ts // 60
        seconds = ts % 60
        peak_info = {
            "start_s": ts,
            "end_s": ts + bucket_s,
            "time_label": f"{minutes:02d}:{seconds:02d}-{minutes:02d}:{seconds+bucket_s:02d}",
            "count": count,
            "keywords": _extract_keywords(contents),
            "sample_quotes": _sample_quotes(contents, n=5),
        }
        peak_details.append(peak_info)

    # Overall keyword frequency
    all_keywords = _extract_keywords([d["content"] for d in dms], top_n=20)

    return {
        "title": title,
        "total_danmaku": len(dms),
        "duration_s": duration,
        "density_per_min": round(density_per_min, 1),
        "bucket_size_s": bucket_s,
        "peaks": peak_details,
        "top_keywords": all_keywords,
        "analysis_note": (
            f"Using {peak_method} method, {bucket_s}s buckets. "
            f"Filtered pool=1: {filter_pool1}."
        ),
    }


def _extract_keywords(texts: list[str], top_n: int = 8) -> list[tuple[str, int]]:
    words: Counter = Counter()
    for text in texts:
        # CJK 2-4 char n-grams
        cjk = re.findall(r"[\u4e00-\u9fff]{2,4}", text)
        for w in cjk:
            words[w] += 1
        # English words >= 3 chars
        eng = re.findall(r"[A-Za-z0-9]{3,}", text)
        for w in eng:
            words[w.upper()] += 1
    return words.most_common(top_n)


def _sample_quotes(contents: list[str], n: int = 5) -> list[str]:
    # Prefer longer, more substantive danmaku
    ranked = sorted(set(contents), key=lambda x: -len(x))
    return ranked[:n]


def render_markdown(analysis: dict[str, Any]) -> str:
    """Render analysis to Markdown."""
    if "error" in analysis:
        return f"# 弹幕分析\n\n❌ {analysis['error']}"

    lines = [
        f"# 弹幕分析：{analysis.get('title', '(未知)')}",
        "",
        "## 概览",
        f"- 总弹幕数：{analysis['total_danmaku']} 条",
        f"- 视频时长：{int(analysis['duration_s'])} 秒",
        f"- 弹幕密度：{analysis['density_per_min']} 条/分钟",
        f"- 分析参数：{analysis['bucket_size_s']}s 时间窗",
        "",
    ]

    peaks = analysis.get("peaks", [])
    if peaks:
        lines.append("## 弹幕密度峰值")
        lines.append("")
        for i, peak in enumerate(peaks, 1):
            lines.append(f"### 峰值 {i}：{peak['time_label']}（{peak['count']} 条）")
            lines.append("")
            if peak.get("keywords"):
                kw_str = "、".join(kw for kw, _ in peak["keywords"][:5])
                lines.append(f"**关键话题**：{kw_str}")
                lines.append("")
            lines.append("**代表性弹幕：**")
            for q in peak.get("sample_quotes", [])[:3]:
                lines.append(f"- 「{q}」")
            lines.append("")

    # Top keywords
    top_kw = analysis.get("top_keywords", [])
    if top_kw:
        lines.append("## 高频关键词")
        lines.append("")
        kw_parts = [f"{kw}({cnt}次)" for kw, cnt in top_kw[:15]]
        lines.append("、".join(kw_parts))
        lines.append("")

    lines.append(f"\n*分析时间：{datetime.now(timezone.utc).isoformat()}*")
    return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────────────────

def cmd_fetch(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Resolve cid(s)
    if args.cid:
        cids = [(args.cid, "", "")]
    else:
        info = get_video_info(args.bvid)
        pages = info.get("pages", [])
        if not pages:
            pages = [{"cid": info["cid"], "part": info["title"], "page": 1}]
        cids = [(p["cid"], p.get("part", ""), p.get("page", 1))
                for p in pages]
        print(f"Video: {info['title']} ({info['duration_s']}s)")

    total = 0
    for cid, part_name, page_num in cids:
        print(f"Fetching danmaku for cid={cid} (page {page_num})...")
        dms = fetch_danmaku_xml(cid)
        total += len(dms)
        print(f"  Got {len(dms)} danmaku")

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        suffix = f"-p{page_num}" if len(cids) > 1 else ""
        out_path = out_dir / f"bilibili-danmaku-{cid}{suffix}-{stamp}.json"

        result = {
            "cid": cid,
            "page": page_num,
            "part_name": part_name,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "count": len(dms),
            "danmaku": dms,
        }
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Done. {total} total danmaku from {len(cids)} page(s).")
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    input_path = Path(args.input)
    danmaku_data = json.loads(input_path.read_text(encoding="utf-8"))
    dms = danmaku_data.get("danmaku", danmaku_data)

    title = danmaku_data.get("part_name") or danmaku_data.get("title") or ""
    if not title:
        # Try to get title from cid
        cid = danmaku_data.get("cid", "")
        if cid:
            title = f"cid={cid}"

    analysis = analyze_danmaku(
        dms,
        title=title,
        bucket_s=args.bucket_s,
        peak_n=args.peak_n,
        peak_method=args.peak_method,
        filter_pool1=not args.no_filter,
    )

    md = render_markdown(analysis)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(md, encoding="utf-8")
        print(f"Report saved → {out_path}")
    else:
        print(md)

    # Also save JSON analysis
    if args.output:
        json_path = Path(args.output).with_suffix(".analysis.json")
        json_path.write_text(
            json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Analysis JSON → {json_path}")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="B站 danmaku collection and analysis"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # fetch
    f = sub.add_parser("fetch", help="Fetch danmaku from B站")
    src = f.add_mutually_exclusive_group(required=True)
    src.add_argument("--bvid", help="B站 video BV id")
    src.add_argument("--cid", type=int, help="B站 video cid")
    f.add_argument("--out-dir", default=".", help="Output directory")

    # analyze
    a = sub.add_parser("analyze", help="Analyze fetched danmaku")
    a.add_argument("--input", required=True, help="Path to danmaku JSON")
    a.add_argument("--output", help="Output Markdown report path")
    a.add_argument("--bucket-s", type=int, default=10, help="Time bucket size (seconds)")
    a.add_argument("--peak-n", type=int, default=5, help="Number of peaks to report")
    a.add_argument("--peak-method", choices=["topn", "zscore"], default="topn")
    a.add_argument("--no-filter", action="store_true",
                   help="Don't filter subtitle danmaku (pool=1)")

    args = parser.parse_args()

    if args.command == "fetch":
        return cmd_fetch(args)
    elif args.command == "analyze":
        return cmd_analyze(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
