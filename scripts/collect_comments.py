#!/usr/bin/env python3
"""
Reference script: B站 comment collection.

Usage:
  python3 scripts/collect_comments.py --bvid BVxxx --sessdata <token> --out-dir <path>
  python3 scripts/collect_comments.py --aid 123456 --cookie-file <path> --out-dir <path>
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import httpx
except ImportError:
    print("Missing httpx. Install: pip install httpx", file=sys.stderr)
    sys.exit(1)


# bvid ↔ aid conversion (standard B站 encoding)
TABLE = "fZodR9XQDSUm21yCkr6zBqiveYah8bt4xsWpHnJE7jL5VG3guMTKNPAwcF"
TR = {TABLE[i]: i for i in range(58)}
_S = [11, 10, 3, 8, 4, 6]
_XOR = 177451812
_ADD = 8728348608


def bvid_to_aid(bvid: str) -> int:
    r = sum(TR[bvid[_S[i]]] * (58 ** i) for i in range(6))
    return (r - _ADD) ^ _XOR


def aid_to_bvid(aid: int) -> str:
    aid = (aid ^ _XOR) + _ADD
    chars = ["B", "V", "1", "", "", "", "", "", "", "4", "", "1", "", "7", "", ""]
    for i in range(6):
        chars[_S[i]] = TABLE[aid // (58 ** i) % 58]
    return "".join(chars)


def load_cookie_from_file(path: str) -> dict[str, str]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    cookies = {}
    for key in ("SESSDATA", "bili_jct"):
        if data.get(key):
            cookies[key] = str(data[key])
    return cookies


def fetch_comments(
    *,
    aid: int,
    cookie: dict[str, str],
    max_pages: int = 10,
    delay_ms: int = 600,
) -> list[dict]:
    """Collect top-level comments from B站 x/v2/reply/main.

    Uses mode=3 (newest-first). Requires valid SESSDATA cookie.
    """
    all_comments: list[dict] = []
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": f"https://www.bilibili.com/video/av{aid}",
    }
    client = httpx.Client(cookies=cookie, headers=headers, timeout=30)

    for pn in range(1, max_pages + 1):
        url = f"https://api.bilibili.com/x/v2/reply/main?type=1&oid={aid}&mode=3&ps=20&pn={pn}"
        resp = client.get(url)
        resp.raise_for_status()
        data = resp.json()

        code = data.get("code")
        if code != 0:
            print(f"API error: code={code}, message={data.get('message')}", file=sys.stderr)
            break

        replies = data.get("data", {}).get("replies") or []
        if not replies:
            break

        for r in replies:
            member = r.get("member") or {}
            content = r.get("content") or {}
            all_comments.append({
                "rpid": r.get("rpid"),
                "mid": r.get("mid"),
                "uname": member.get("uname"),
                "message": content.get("message"),
                "ctime": r.get("ctime"),
                "like": r.get("like"),
                "rcount": r.get("rcount"),
            })

        cursor = data.get("data", {}).get("cursor", {})
        # Stop if we've collected as many top-level as there are total (approximate)
        if len(all_comments) >= cursor.get("all_count", 0):
            break

        if pn < max_pages:
            time.sleep(delay_ms / 1000)

    client.close()
    return all_comments


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect B站 video comments")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--bvid", help="B站 video BV id")
    src.add_argument("--aid", type=int, help="B站 video aid")

    cookie_src = parser.add_mutually_exclusive_group(required=True)
    cookie_src.add_argument("--sessdata", help="B站 SESSDATA cookie value")
    cookie_src.add_argument("--cookie-file", help="Path to credentials JSON with SESSDATA")

    parser.add_argument("--out-dir", default=".", help="Output directory for raw JSON")
    parser.add_argument("--max-pages", type=int, default=10, help="Max pages to fetch")
    parser.add_argument("--delay-ms", type=int, default=600, help="Delay between pages (ms)")

    args = parser.parse_args()

    # Resolve aid
    if args.aid:
        aid = args.aid
        bvid = aid_to_bvid(aid)
    else:
        bvid = args.bvid
        aid = bvid_to_aid(bvid)

    # Resolve cookie
    if args.cookie_file:
        cookie = load_cookie_from_file(args.cookie_file)
    else:
        cookie = {"SESSDATA": args.sessdata}

    if not cookie.get("SESSDATA"):
        print("Error: no SESSDATA found", file=sys.stderr)
        return 1

    print(f"Collecting comments for {bvid} (aid={aid})...")
    comments = fetch_comments(aid=aid, cookie=cookie, max_pages=args.max_pages, delay_ms=args.delay_ms)

    # Save
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = out_dir / f"bilibili-video-comments-{bvid}-{stamp}.json"

    result = {
        "bvid": bvid,
        "aid": aid,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "comment_count": len(comments),
        "comments": comments,
    }
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {len(comments)} comments → {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
