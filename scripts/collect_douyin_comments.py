#!/usr/bin/env python3
"""
Collect comments for a 抖音 video by aweme_id.

Uses Playwright with an authenticated storage_state so the comment-list API
fires with a valid a-bogus signature (generated client-side by 抖音's JS).
We intercept those API responses via page.on("response") rather than calling
the API ourselves — replicating a-bogus outside the browser is fragile.

Usage:
  scripts/collect_douyin_comments.py --aweme-id <id> \
    --storage-state social/_secrets/<account>/douyin/default.storage_state.json \
    --out-dir social/<account>/douyin/raw \
    [--max-pages 20] [--chromium /path/to/Chromium]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("Missing playwright. Install: pip install playwright", file=sys.stderr)
    sys.exit(1)


DEFAULT_CHROMIUM = (
    "/Users/xxh/Library/Caches/ms-playwright/chromium-1217/"
    "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
)


async def collect(
    *,
    aweme_id: str,
    storage_state: Path,
    out_dir: Path,
    chromium: str,
    max_pages: int,
    headless: bool,
) -> dict[str, Any]:
    state = json.loads(storage_state.read_text(encoding="utf-8"))
    cookies = state.get("cookies") or []

    collected: dict[int, dict] = {}  # cid → comment
    raw_pages: list[dict] = []
    seen_cursors: set[Any] = set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path=chromium,
            headless=headless,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        ctx = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
        )
        await ctx.add_cookies(cookies)
        page = await ctx.new_page()

        async def on_response(resp):
            url = resp.url
            if "/aweme/v1/web/comment/list" not in url:
                return
            try:
                body = await resp.json()
            except Exception:
                return
            raw_pages.append({"url": url, "status": resp.status})
            for c in (body.get("comments") or []):
                cid = c.get("cid")
                if cid and cid not in collected:
                    user = c.get("user") or {}
                    collected[cid] = {
                        "cid": cid,
                        "text": c.get("text"),
                        "create_time": c.get("create_time"),
                        "digg_count": c.get("digg_count"),
                        "reply_comment_total": c.get("reply_comment_total"),
                        "user_nickname": user.get("nickname"),
                        "user_short_id": user.get("short_id"),
                        "ip_label": c.get("ip_label"),
                    }
            cursor = body.get("cursor")
            seen_cursors.add(cursor)

        page.on("response", on_response)

        # Navigate to public video page; .douyin.com cookies make us logged in
        await page.goto(
            f"https://www.douyin.com/video/{aweme_id}",
            timeout=60000,
            wait_until="domcontentloaded",
        )
        # Give the SPA time to render and fire the first comment-list request.
        # We don't wait_for_response on a specific URL because the initial fire
        # is sometimes triggered only after a scroll nudge, which we do below.
        await page.wait_for_timeout(5000)

        # Scroll the comment panel to trigger lazy-load. 抖音's DOM class names
        # rotate frequently so JS-selector scrolling is unreliable; mouse wheel
        # events on the comments area (right side of the player) survive across
        # DOM changes. We also try scrolling the largest tall scrollable
        # element as a belt-and-suspenders fallback.
        last_seen = len(collected)
        stable_rounds = 0
        await page.mouse.move(1100, 500)
        for _ in range(max_pages * 3):
            await page.mouse.wheel(0, 1500)
            # Also try scrolling the tallest content-scrollable element.
            await page.evaluate("""() => {
                let best = null;
                document.querySelectorAll('*').forEach(el => {
                    const cs = getComputedStyle(el);
                    if ((cs.overflowY === 'auto' || cs.overflowY === 'scroll') &&
                        el.scrollHeight > el.clientHeight + 50 &&
                        el.getAttribute('data-e2e') !== 'douyin-navigation') {
                        if (!best || el.scrollHeight > best.scrollHeight) best = el;
                    }
                });
                if (best) best.scrollTop = best.scrollHeight;
            }""")
            await page.wait_for_timeout(1500)

            if len(collected) == last_seen:
                stable_rounds += 1
                if stable_rounds >= 4:
                    break
            else:
                stable_rounds = 0
                last_seen = len(collected)

        await browser.close()

    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = out_dir / f"douyin-video-comments-{aweme_id}-{stamp}.json"
    result = {
        "aweme_id": aweme_id,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "comment_count": len(collected),
        "api_pages_intercepted": len(raw_pages),
        "comments": sorted(collected.values(), key=lambda c: -(c.get("digg_count") or 0)),
    }
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "count": len(collected), "pages": len(raw_pages), "path": str(out_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect 抖音 video comments")
    parser.add_argument("--aweme-id", required=True, help="抖音 video aweme_id")
    parser.add_argument("--storage-state", required=True, help="Playwright storage_state JSON")
    parser.add_argument("--out-dir", default=".", help="Output directory")
    parser.add_argument("--max-pages", type=int, default=20, help="Max comment pages")
    parser.add_argument("--chromium", default=DEFAULT_CHROMIUM, help="Chromium executable path")
    parser.add_argument("--headless", action="store_true", help="Run headless (default: headed)")
    args = parser.parse_args()

    storage = Path(args.storage_state)
    if not storage.exists():
        print(f"Storage state not found: {storage}", file=sys.stderr)
        return 1

    result = asyncio.run(collect(
        aweme_id=args.aweme_id,
        storage_state=storage,
        out_dir=Path(args.out_dir),
        chromium=args.chromium,
        max_pages=args.max_pages,
        headless=args.headless,
    ))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
