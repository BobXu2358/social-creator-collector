from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import async_playwright


KNOWN_PUBLIC_PROFILES = {
    "xgame": {
        "sec_user_id": "MS4wLjABAAAABh9XdGzxhMtnrq511GPIWQ07vxu6Ue_g1EKWyuzrmt_yZFvhEF5vnyAZjIPaMA6a",
        "user_url": "https://www.douyin.com/user/MS4wLjABAAAABh9XdGzxhMtnrq511GPIWQ07vxu6Ue_g1EKWyuzrmt_yZFvhEF5vnyAZjIPaMA6a",
    }
}


def _profile_path(secret_root: Path, account_profile: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", account_profile):
        raise ValueError("account_profile must be a simple profile name")
    return secret_root / f"{account_profile}.profile.json"


def load_profile(secret_root: Path, business_id: str, account_profile: str) -> dict[str, str]:
    path = _profile_path(secret_root, account_profile)
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        data = KNOWN_PUBLIC_PROFILES.get(business_id, {})
    sec_user_id = str(data.get("sec_user_id") or "").strip()
    user_url = str(data.get("user_url") or "").strip()
    if not sec_user_id and user_url:
        sec_user_id = _sec_user_id_from_url(user_url)
    if not user_url and sec_user_id:
        user_url = f"https://www.douyin.com/user/{sec_user_id}"
    return {
        "profile_path": str(path),
        "profile_exists": str(path.exists()).lower(),
        "sec_user_id": sec_user_id,
        "user_url": user_url,
    }


def _sec_user_id_from_url(url: str) -> str:
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) >= 2 and parts[0] == "user":
        return parts[1]
    return ""


def build_plan(secret_root: Path, business_id: str, account_profile: str) -> dict[str, Any]:
    profile = load_profile(secret_root, business_id, account_profile)
    return {
        "backend": "douyin-public-playwright",
        "mode": "public_profile_posts",
        "profile": profile,
        "read_only": True,
        "requires_login": False,
    }


def collect_public_posts(
    *,
    business_id: str,
    business_name: str,
    secret_root: Path,
    raw_dir: Path,
    account_profile: str,
    max_scrolls: int = 8,
) -> dict[str, Any]:
    return asyncio.run(
        _collect_public_posts_async(
            business_id=business_id,
            business_name=business_name,
            secret_root=secret_root,
            raw_dir=raw_dir,
            account_profile=account_profile,
            max_scrolls=max_scrolls,
        )
    )


async def _collect_public_posts_async(
    *,
    business_id: str,
    business_name: str,
    secret_root: Path,
    raw_dir: Path,
    account_profile: str,
    max_scrolls: int,
) -> dict[str, Any]:
    profile = load_profile(secret_root, business_id, account_profile)
    user_url = profile["user_url"]
    sec_user_id = profile["sec_user_id"]
    if not user_url or not sec_user_id:
        raise RuntimeError("Douyin profile missing sec_user_id/user_url; create <profile>.profile.json under secret root")

    collected_at = datetime.now(timezone.utc).isoformat()
    responses: list[dict[str, Any]] = []
    posts_by_id: dict[str, dict[str, Any]] = {}
    user_info: dict[str, Any] = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path="/usr/bin/chromium",
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1365, "height": 900},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        page = await context.new_page()

        async def on_response(resp):
            url = resp.url
            if "/aweme/v1/web/user/profile/other/" not in url and "/aweme/v1/web/aweme/post/" not in url:
                return
            item: dict[str, Any] = {"url": url, "status": resp.status}
            try:
                text = await resp.text()
                obj = json.loads(text)
                item["status_code"] = obj.get("status_code")
                item["status_msg"] = obj.get("status_msg")
                if "/user/profile/other/" in url:
                    nonlocal user_info
                    user_info = _sanitize_user(obj.get("user") or {})
                    item["kind"] = "profile"
                else:
                    item["kind"] = "post"
                    item["has_more"] = obj.get("has_more")
                    item["max_cursor"] = obj.get("max_cursor")
                    awemes = obj.get("aweme_list") or []
                    item["aweme_count"] = len(awemes)
                    for aweme in awemes:
                        post = _aweme_to_row(
                            aweme,
                            business_id=business_id,
                            platform="douyin",
                            account_id=sec_user_id,
                            collected_at=collected_at,
                        )
                        if post["content_id"]:
                            posts_by_id[post["content_id"]] = post
                responses.append(item)
            except Exception as exc:
                item["error"] = f"{type(exc).__name__}: {exc}"
                responses.append(item)

        page.on("response", on_response)
        try:
            await page.goto(user_url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(8000)
            for _ in range(max_scrolls):
                await page.mouse.wheel(0, 2400)
                await page.wait_for_timeout(1800)
            body_prefix = (await page.locator("body").inner_text(timeout=5000))[:2000]
            title = await page.title()
            final_url = page.url
        finally:
            await context.close()
            await browser.close()

    posts = sorted(posts_by_id.values(), key=lambda r: r.get("published_at") or "", reverse=True)
    result = {
        "business_id": business_id,
        "business_name": business_name,
        "platform": "douyin",
        "account_id": sec_user_id,
        "account_profile": account_profile,
        "source_url": user_url,
        "page_title": title,
        "final_url": final_url,
        "collected_at": collected_at,
        "profile": user_info,
        "item_count": len(posts),
        "items": posts,
        "network_summary": responses,
        "body_prefix": body_prefix,
    }
    raw_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = raw_dir / f"douyin-public-posts-{stamp}.json"
    result["raw_path"] = str(out)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def _sanitize_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "uid": user.get("uid"),
        "sec_uid": user.get("sec_uid"),
        "nickname": user.get("nickname"),
        "unique_id": user.get("unique_id"),
        "short_id": user.get("short_id"),
        "signature": user.get("signature"),
        "follower_count": user.get("follower_count"),
        "following_count": user.get("following_count"),
        "total_favorited": user.get("total_favorited"),
        "aweme_count": user.get("aweme_count"),
        "ip_location": user.get("ip_location"),
    }


def _aweme_to_row(aweme: dict[str, Any], *, business_id: str, platform: str, account_id: str, collected_at: str) -> dict[str, Any]:
    ts = aweme.get("create_time")
    published_at = None
    if isinstance(ts, (int, float)) and ts:
        published_at = datetime.fromtimestamp(int(ts), timezone.utc).isoformat()
    stats = aweme.get("statistics") or {}
    video = aweme.get("video") or {}
    cover = video.get("cover") or {}
    share = aweme.get("share_info") or {}
    return {
        "business_id": business_id,
        "platform": platform,
        "account_id": account_id,
        "content_id": str(aweme.get("aweme_id") or ""),
        "content_title": aweme.get("desc"),
        "published_at": published_at,
        "collected_at": collected_at,
        "metrics": {
            "digg_count": stats.get("digg_count"),
            "comment_count": stats.get("comment_count"),
            "share_count": stats.get("share_count"),
            "collect_count": stats.get("collect_count"),
            "play_count": stats.get("play_count"),
        },
        "source_url": share.get("share_url") or (f"https://www.douyin.com/video/{aweme.get('aweme_id')}" if aweme.get("aweme_id") else None),
        "raw_ref": None,
        "duration_ms": aweme.get("duration"),
        "cover_url": (cover.get("url_list") or [None])[0],
    }
