"""Douyin: Playwright-driven creator-center and public-data collection.

Why a browser is unavoidable here:
  * login state + the per-request ``a-bogus`` signature are produced by Douyin's
    own JS — replicating them outside the browser is fragile, so work_list and
    comment APIs are fired *from inside the page* (``page.evaluate(fetch)`` /
    response interception).
  * per-video fan growth (粉丝增量) has no API at all — it exists only in the DOM
    of the 投稿列表 table, so we scrape it.

Commands: check-cookies, import-cookies, worklist, fan-trend, fan-growth, comments.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import schema
from .browser import BUNDLED_HINT, launch_kwargs
from .paths import TZ, CollectorError, output_dirs

_CTX = dict(
    viewport={"width": 1365, "height": 900},
    locale="zh-CN",
    timezone_id="Asia/Shanghai",
    user_agent=(
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
)


def _import_playwright():
    try:
        from playwright.async_api import async_playwright  # noqa: WPS433
    except ImportError as exc:  # pragma: no cover - env dependent
        raise CollectorError("playwright not installed — pip install playwright") from exc
    return async_playwright


async def _launch(p, chromium: str | None, *, headless: bool = True):
    try:
        return await p.chromium.launch(headless=headless, **launch_kwargs(chromium))
    except Exception as exc:  # bundled browser missing → actionable message
        raise CollectorError(f"{exc}\n\n{BUNDLED_HINT}") from exc


@asynccontextmanager
async def _browser(p, chromium: str | None, *, headless: bool = True):
    """Launch a browser and guarantee it's closed even if collection raises midway.

    Without this, any exception between launch and the trailing ``browser.close()``
    leaked the browser process (and, for headed login, a stranded window).
    """
    browser = await _launch(p, chromium, headless=headless)
    try:
        yield browser
    finally:
        await browser.close()


def _stamp() -> str:
    return datetime.now(TZ).strftime("%Y%m%d-%H%M%S")


async def _body_text(page, *, timeout_ms: int = 10000, limit: int = 3000) -> str:
    try:
        await page.wait_for_function(
            "() => document.body && document.body.innerText && document.body.innerText.trim().length > 0",
            timeout=timeout_ms,
        )
        return (await page.locator("body").inner_text(timeout=2000))[:limit]
    except Exception:
        return ""


async def _body_text_with_markers(
    page, markers: tuple[str, ...], *, timeout_ms: int = 10000, limit: int = 3000,
) -> str:
    try:
        await page.wait_for_function(
            """markers => {
                const text = (document.body && document.body.innerText || '').trim();
                return text && markers.some(marker => text.includes(marker));
            }""",
            list(markers),
            timeout=timeout_ms,
        )
    except Exception:
        pass
    return await _body_text(page, timeout_ms=2000, limit=limit)


async def _wait_for_selector_or_short_fallback(page, selector: str, *, timeout_ms: int = 10000) -> None:
    try:
        await page.wait_for_selector(selector, timeout=timeout_ms)
    except Exception:
        await page.wait_for_timeout(1000)


def _chmod_600(path: Path) -> None:
    """Best-effort lock down a secret file (storage state holds session cookies).
    Mirrors what Bilibili login does for its credential file; noop on Windows."""
    try:
        path.chmod(0o600)
    except OSError:
        pass


# ── cookies ──────────────────────────────────────────────────────────────

def _normalize_cookie(c: dict[str, Any]) -> dict[str, Any]:
    nc = {k: v for k, v in c.items()
          if k in {"name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite"}}
    nc.setdefault("path", "/")
    if "sameSite" in nc:
        s = str(nc["sameSite"]).lower()
        nc["sameSite"] = {
            "strict": "Strict",
            "lax": "Lax",
            "none": "None",
            "no_restriction": "None",
        }.get(s, "Lax")
    if nc.get("expires") in (None, "", 0):
        nc.pop("expires", None)
    return nc


def _load_cookie_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise CollectorError(f"missing Douyin cookie json: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    cookies = data if isinstance(data, list) else data.get("cookies") if isinstance(data, dict) else None
    if not isinstance(cookies, list):
        raise CollectorError("Cookie-Editor export must be a JSON list or a {cookies:[...]} object")
    return cookies


def check_cookies(*, path: Path) -> dict[str, Any]:
    cookies = _load_cookie_list(path)
    names = {str(c.get("name", "")) for c in cookies if isinstance(c, dict)}
    important = [n for n in ("sessionid", "sessionid_ss", "sid_guard", "uid_tt", "uid_tt_ss",
                             "passport_csrf_token") if n in names]
    douyin_cookie_count = sum(
        1
        for c in cookies
        if isinstance(c, dict) and _is_douyin_cookie_domain(str(c.get("domain", "")))
    )
    return {
        "ok": True,
        "path": str(path),
        "cookie_count": len(cookies),
        "douyin_domain_cookie_count": douyin_cookie_count,
        "other_domain_cookie_count": len(cookies) - douyin_cookie_count,
        "important_names_present": important,
    }


def _is_douyin_cookie_domain(domain: str) -> bool:
    host = domain.lstrip(".").lower()
    return host == "douyin.com" or host.endswith(".douyin.com") or host == "iesdouyin.com" or host.endswith(".iesdouyin.com")


def import_cookies(*, cookies_path: Path, state_path: Path, chromium: str | None,
                   nickname: str | None, douyin_id: str | None) -> dict[str, Any]:
    return asyncio.run(_import_cookies(cookies_path, state_path, chromium, nickname, douyin_id))


_IMPORT_SUCCESS_MARKERS = (
    "创作者服务中心", "创作者中心", "作品管理", "内容管理", "数据中心", "发布作品",
)


async def _probe_creator_worklist(page) -> dict[str, Any]:
    url = ("/janus/douyin/creator/pc/work_list?scene=star_atlas"
           "&device_platform=android&aid=1128&status=0&count=1&max_cursor=0")
    try:
        return await page.evaluate(
            """async (url) => {
                const r = await fetch(url, {credentials:'same-origin'});
                const t = await r.text();
                try { return {status:r.status, json:JSON.parse(t)}; }
                catch(e) { return {status:r.status, textPrefix:t.slice(0,200)}; }
            }""", url)
    except Exception:
        return {"error": "fetch_failed"}


def _api_probe_ok(probe: dict[str, Any]) -> bool:
    try:
        status_ok = int(probe.get("status", 0)) < 400
    except Exception:
        status_ok = False
    js = probe.get("json")
    return (
        status_ok
        and isinstance(js, dict)
        and not _api_error(js)
        and any(k in js for k in ("aweme_list", "has_more", "max_cursor", "cursor"))
    )


def _import_cookie_verification(
    body: str,
    probe: dict[str, Any],
    *,
    nickname: str | None,
    douyin_id: str | None,
) -> dict[str, Any]:
    api_error = _api_error(probe.get("json") or {})
    out = {
        "ok": False,
        "login_page_seen": _looks_like_login_page(body),
        "creator_marker_seen": any(marker in body for marker in _IMPORT_SUCCESS_MARKERS),
        "api_ok": _api_probe_ok(probe),
        "api_status": probe.get("status"),
        "api_error": api_error,
        "nickname_seen": bool(nickname and nickname in body),
        "douyin_id_seen": bool(douyin_id and douyin_id in body),
    }
    out["ok"] = (
        not out["login_page_seen"]
        and (
            out["creator_marker_seen"]
            or out["api_ok"]
            or out["nickname_seen"]
            or out["douyin_id_seen"]
        )
    )
    return out


async def _import_cookies(cookies_path, state_path, chromium, nickname, douyin_id) -> dict[str, Any]:
    cookies = _load_cookie_list(cookies_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_state_path = state_path.with_name(f".{state_path.name}.tmp")
    if tmp_state_path.exists():
        tmp_state_path.unlink()
    verification: dict[str, Any] = {}
    async_playwright = _import_playwright()
    try:
        async with async_playwright() as p, _browser(p, chromium) as browser:
            ctx = await browser.new_context(**_CTX)
            await ctx.add_cookies([_normalize_cookie(c) for c in cookies])
            page = await ctx.new_page()
            await page.goto("https://creator.douyin.com/creator-micro/home",
                            wait_until="domcontentloaded", timeout=60000)
            body = await _body_text(page, timeout_ms=12000)
            probe = await _probe_creator_worklist(page)
            verification = _import_cookie_verification(
                body, probe, nickname=nickname, douyin_id=douyin_id,
            )
            if not verification["ok"]:
                if verification["login_page_seen"]:
                    msg = (
                        "Douyin still on a login page after importing cookies — the export is stale. "
                        "Re-export from a freshly logged-in creator.douyin.com session."
                    )
                else:
                    msg = (
                        "Douyin cookie import did not produce a positive logged-in verification "
                        "(no creator marker/account hint/API success). Re-export cookies from "
                        "creator.douyin.com, or use QR login."
                    )
                raise CollectorError(msg)
            await ctx.storage_state(path=str(tmp_state_path))
        _chmod_600(tmp_state_path)
        tmp_state_path.replace(state_path)
        _chmod_600(state_path)
    except Exception:
        if tmp_state_path.exists():
            tmp_state_path.unlink()
        raise
    return {
        "ok": True,
        "storage_state": str(state_path),
        "verification": verification,
    }


# ── QR login (headed browser; Douyin renders its own QR in the page) ──────

def login(*, ws: Path, account: str, state_path: Path, chromium: str | None,
          timeout_s: int = 180) -> dict[str, Any]:
    return asyncio.run(_login(state_path, chromium, timeout_s))


async def _login(state_path, chromium, timeout_s) -> dict[str, Any]:
    """Open a real (headed) browser to creator.douyin.com; the user scans the QR
    Douyin shows there. We poll for the ``sessionid`` cookie, then dump the full
    storage state. Headless/remote QR is rejected by Douyin risk control, so this
    deliberately runs headed — it needs a desktop session.
    """
    state_path.parent.mkdir(parents=True, exist_ok=True)
    async_playwright = _import_playwright()
    async with async_playwright() as p, _browser(p, chromium, headless=False) as browser:
        ctx = await browser.new_context(**_CTX)
        page = await ctx.new_page()
        await page.goto("https://creator.douyin.com/", wait_until="domcontentloaded", timeout=60000)
        print("抖音：浏览器窗口已打开，请用抖音 App 扫码登录…", file=sys.stderr)
        logged_in = False
        waited = 0
        while waited < timeout_s * 1000:
            names = {c["name"] for c in await ctx.cookies()}
            if "sessionid" in names or "sessionid_ss" in names:
                logged_in = True
                break
            await page.wait_for_timeout(2000)
            waited += 2000
        if not logged_in:
            raise CollectorError(
                "Douyin QR login timed out (no sessionid). Retry, or fall back to "
                "Cookie-Editor export + import-cookies."
            )
        await ctx.storage_state(path=str(state_path))
    _chmod_600(state_path)
    return {"ok": True, "storage_state": str(state_path), "method": "qr-login"}


# ── work list (basic per-video metrics) ──────────────────────────────────

def _pick(obj: dict[str, Any], *names: str) -> Any:
    for n in names:
        if isinstance(obj, dict) and obj.get(n) not in (None, ""):
            return obj[n]
    return None


def _ts_to_str(ts: Any) -> str:
    try:
        t = int(ts)
        t = t // 1000 if t > 10_000_000_000 else t
        return datetime.fromtimestamp(t, TZ).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


def _iso(ts: Any) -> str | None:
    try:
        t = int(ts)
        t = t // 1000 if t > 10_000_000_000 else t
        return datetime.fromtimestamp(t, TZ).isoformat()
    except Exception:
        return None


def _duration_seconds(value: Any) -> float | None:
    try:
        duration = float(value)
    except Exception:
        return None
    if duration <= 0:
        return None
    # work_list currently reports milliseconds; keep already-second values intact.
    if duration > 10_000:
        duration = duration / 1000
    return int(duration) if duration.is_integer() else round(duration, 3)


def _first_url(value: Any) -> str | None:
    if isinstance(value, str):
        return value or None
    if isinstance(value, list):
        for item in value:
            found = _first_url(item)
            if found:
                return found
    if isinstance(value, dict):
        for key in ("url", "uri", "cover_url", "display_url", "origin_url", "url_list", "urlList", "UrlList"):
            found = _first_url(value.get(key))
            if found:
                return found
    return None


def _aweme_canonical(n: dict[str, Any], account: str, captured_at: str) -> dict[str, Any]:
    """Map an internal normalized aweme to a canonical video row."""
    row = schema.video_row(
        platform="douyin", account=account, content_id=n.get("aweme_id"),
        title=n.get("title"), published_at=_iso(n.get("create_time")),
        captured_at=captured_at, source_url=n.get("url"),
        metrics={
            "plays": n.get("play"), "likes": n.get("like"),
            "comments": n.get("comment"), "shares": n.get("share"),
            "collects": n.get("collect"),
        })
    for key in ("duration_s", "cover_url", "work_type", "status", "visibility", "audit_status"):
        if n.get(key) not in (None, ""):
            row[key] = n[key]
    platform_fields = {
        "forward": n.get("forward"),
    }
    platform_fields = {k: v for k, v in platform_fields.items() if v not in (None, "")}
    if platform_fields:
        row["platform_fields"] = platform_fields
    return row


def _looks_like_login_page(body: str) -> bool:
    markers = ("扫码登录", "验证码登录", "登录/注册", "手机号登录", "抖音号登录")
    return any(m in body for m in markers)


_WORKLIST_READY_MARKERS = (
    "作品管理", "内容管理", "发布作品", "作品", "扫码登录", "验证码登录", "登录/注册", "手机号登录", "抖音号登录",
)


def _api_error(js: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(js, dict):
        return {}
    out = {}
    for key in ("status_code", "status_msg", "error_code", "error_msg", "code", "message", "msg"):
        value = js.get(key)
        if value not in (None, "", 0, "0"):
            out[key] = value
    return out


def _worklist_page_meta(pn: int, obj: dict[str, Any], js: dict[str, Any], aw: list) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "pn": pn,
        "status": obj.get("status"),
        "count": len(aw),
        "has_more": js.get("has_more"),
    }
    if obj.get("textPrefix"):
        meta["textPrefix"] = obj["textPrefix"]
    api_error = _api_error(js)
    if api_error:
        meta["api_error"] = api_error
    return meta


def _worklist_likely_login_required(landing_body: str, pages_meta: list[dict[str, Any]]) -> bool:
    if _looks_like_login_page(landing_body):
        return True
    for page in pages_meta:
        err = page.get("api_error") or {}
        code = err.get("status_code") or err.get("error_code") or err.get("code")
        msg = " ".join(str(err.get(k) or "") for k in ("status_msg", "error_msg", "message", "msg"))
        if str(code) == "8" or any(x in msg.lower() for x in ("login", "登录", "not login")):
            return True
    return False


_OVERVIEW_DAYS_TYPE = {7: 1, 15: 2, 30: 3}


def _overview_days_type(days: int) -> int:
    try:
        return _OVERVIEW_DAYS_TYPE[int(days)]
    except Exception as exc:
        raise CollectorError("Douyin fan-trend supports --days 7, 15, or 30") from exc


def _counts_by_date(block: dict[str, Any]) -> dict[str, int | None]:
    rows = block.get("option_list") if isinstance(block, dict) else []
    if not isinstance(rows, list):
        return {}
    out: dict[str, int | None] = {}
    for row in rows:
        if not isinstance(row, dict) or not row.get("date"):
            continue
        out[str(row["date"])] = _parse_int(row.get("count"))
    return out


_OVERVIEW_RELATED_SERIES = {
    "cancel_fans": ("unfollow_count", "unfollows"),
    "profile": ("profile_views", "profile views"),
    "account_search": ("account_searches", "account searches"),
    "post_search": ("post_searches", "post searches"),
    "play": ("plays", "plays"),
    "fans": ("follower_plays", "follower plays"),
    "digg": ("likes", "likes"),
    "comment": ("comments", "comments"),
    "share": ("shares", "shares"),
}


def _douyin_fan_trend_rows(js: dict[str, Any], account: str, captured_at: str) -> list[dict[str, Any]]:
    data = js.get("data") if isinstance(js, dict) else {}
    if not isinstance(data, dict):
        return []
    new_fans = data.get("new_fans") or {}
    related = {
        out_key: _counts_by_date(data.get(api_key) or {})
        for api_key, (out_key, _label) in _OVERVIEW_RELATED_SERIES.items()
    }
    option_list = new_fans.get("option_list") if isinstance(new_fans, dict) else []
    if not isinstance(option_list, list):
        return []
    rows = []
    for row in option_list:
        if not isinstance(row, dict) or not row.get("date"):
            continue
        fan_inc = _parse_int(row.get("count"))
        if fan_inc is None:
            continue
        item = schema.fan_trend_row(
            platform="douyin", account=account, date=str(row["date"]),
            fan_inc=fan_inc, captured_at=captured_at,
        )
        for out_key, by_date in related.items():
            value = by_date.get(str(row["date"]))
            if value is not None:
                item[out_key] = value
        if row.get("last_day_incr_rate") not in (None, ""):
            item["fan_inc_last_day_incr_rate"] = row["last_day_incr_rate"]
        rows.append(item)
    return rows


def _douyin_overview_metric_labels() -> dict[str, str]:
    return {
        "fan_inc": "daily net new fans",
        **{out_key: label for _api_key, (out_key, label) in _OVERVIEW_RELATED_SERIES.items()},
    }


def fan_trend(*, ws: Path, account: str, state_path: Path, days: int,
              chromium: str | None) -> dict[str, Any]:
    return asyncio.run(_fan_trend(ws, account, state_path, days, chromium))


async def _fan_trend(ws, account, state_path, days, chromium) -> dict[str, Any]:
    if not state_path.exists():
        raise CollectorError(f"missing Douyin storage state; run login/import-cookies first: {state_path}")
    last_days_type = _overview_days_type(days)
    async_playwright = _import_playwright()
    async with async_playwright() as p, _browser(p, chromium) as browser:
        ctx = await browser.new_context(storage_state=str(state_path), **_CTX)
        page = await ctx.new_page()
        await page.goto("https://creator.douyin.com/creator-micro/home",
                        wait_until="domcontentloaded", timeout=60000)
        await _body_text_with_markers(
            page, ("净增粉丝", "数据中心", "扫码登录", "验证码登录", "登录/注册"), timeout_ms=10000,
        )
        obj = await page.evaluate(
            """async (lastDaysType) => {
                const url = `/aweme/janus/creator/data/overview/all/?last_days_type=${lastDaysType}`;
                const r = await fetch(url, {credentials:'same-origin'});
                const t = await r.text();
                try { return {status:r.status, json:JSON.parse(t)}; }
                catch(e) { return {status:r.status, textPrefix:t.slice(0,500)}; }
            }""", last_days_type)

    js = obj.get("json") or {}
    err = _api_error(js)
    if obj.get("status", 0) >= 400 or err:
        raise CollectorError(f"Douyin fan-trend API error status={obj.get('status')} detail={err or obj.get('textPrefix')}")
    captured = datetime.now(TZ).isoformat()
    rows = _douyin_fan_trend_rows(js, account, captured)
    if not rows:
        raise CollectorError("no Douyin fan trend returned (storage state may be expired or creator data unavailable)")

    raw, processed = output_dirs(ws, account, "douyin")
    stamp = _stamp()
    start, end = rows[0]["date"], rows[-1]["date"]
    result = {
        "schema_version": schema.SCHEMA_VERSION,
        "account": account, "platform": "douyin",
        "source": "Douyin creator center /aweme/janus/creator/data/overview/all",
        "captured_at": captured,
        "range": {"start": start, "end": end, "days": days, "last_days_type": last_days_type},
        "metric_labels": _douyin_overview_metric_labels(),
        "fan_total": sum(r["fan_inc"] for r in rows),
        "fan_trend": rows,
    }
    jp = raw / f"douyin-fan-trend-{days}d-{stamp}.json"
    mp = processed / f"douyin-fan-trend-{days}d-{stamp}.md"
    jp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"# {account} Douyin fan trend ({days} days)", "",
             f"Range: {start} -> {end}",
             f"Fan total: {result['fan_total']:,}", "",
             "| Date | Net fans | Unfollows | Profile views | Account searches | Post searches | Plays | Follower plays |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for r in rows:
        lines.append(f"| {r['date']} | {_fmt(r['fan_inc'])} | {_fmt(r.get('unfollow_count'))} | "
                     f"{_fmt(r.get('profile_views'))} | {_fmt(r.get('account_searches'))} | "
                     f"{_fmt(r.get('post_searches'))} | {_fmt(r.get('plays'))} | "
                     f"{_fmt(r.get('follower_plays'))} |")
    mp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"ok": True, "json": str(jp), "markdown": str(mp),
            "fan_total": result["fan_total"], "rows": len(rows)}


def _normalize_aweme(a: dict[str, Any]) -> dict[str, Any]:
    stat = a.get("Statistics") or a.get("statistics") or {}
    video = a.get("Video") or a.get("video") or {}
    item = {
        "aweme_id": _pick(a, "AwemeId", "aweme_id", "item_id", "id"),
        "title": _pick(a, "Desc", "desc", "Title", "title") or "",
        "create_time": _pick(a, "CreateTime", "create_time"),
        "duration_s": _duration_seconds(_pick(a, "Duration", "duration", "duration_ms", "video_duration")
                                        or _pick(video, "Duration", "duration")),
        "cover_url": _first_url(_pick(a, "Cover", "cover", "cover_url", "display_url")
                                or _pick(video, "Cover", "cover", "cover_url", "display_url")),
        "work_type": _pick(a, "AwemeType", "aweme_type", "MediaType", "media_type",
                           "ItemType", "item_type", "ContentType", "content_type", "type"),
        "status": _pick(a, "Status", "status", "ItemStatus", "item_status"),
        "visibility": _pick(a, "Visibility", "visibility", "Visible", "visible", "Permission", "permission"),
        "audit_status": _pick(a, "AuditStatus", "audit_status", "ReviewStatus", "review_status"),
    }
    item["create_time_str"] = _ts_to_str(item["create_time"])
    for out, names in {
        "play": ["PlayCnt", "play_count", "play", "view_count"],
        "like": ["DiggCnt", "digg_count", "like_count"],
        "comment": ["CommentCnt", "comment_count"],
        "share": ["ShareCnt", "share_count"],
        "forward": ["ForwardCnt", "forward_count", "forward"],
        "collect": ["CollectCnt", "collect_count"],
    }.items():
        item[out] = _pick(a, *names) or _pick(stat, *names)
    if item["aweme_id"]:
        item["url"] = f"https://www.douyin.com/video/{item['aweme_id']}"
    return item


def worklist(*, ws: Path, account: str, state_path: Path, days: int, max_pages: int,
             chromium: str | None) -> dict[str, Any]:
    return asyncio.run(_worklist(ws, account, state_path, days, max_pages, chromium))


async def _worklist(ws, account, state_path, days, max_pages, chromium) -> dict[str, Any]:
    if not state_path.exists():
        raise CollectorError(f"missing Douyin storage state; run import-cookies first: {state_path}")
    async_playwright = _import_playwright()
    all_items: list[dict[str, Any]] = []
    pages_meta: list[dict[str, Any]] = []
    landing_body = ""
    async with async_playwright() as p, _browser(p, chromium) as browser:
        ctx = await browser.new_context(storage_state=str(state_path), **_CTX)
        page = await ctx.new_page()
        await page.goto("https://creator.douyin.com/creator-micro/content/manage",
                        wait_until="domcontentloaded", timeout=60000)
        landing_body = await _body_text_with_markers(page, _WORKLIST_READY_MARKERS, timeout_ms=10000)
        cursor = 0
        for pn in range(1, max_pages + 1):
            url = ("/janus/douyin/creator/pc/work_list?scene=star_atlas"
                   f"&device_platform=android&aid=1128&status=0&count=12&max_cursor={cursor}")
            obj = await page.evaluate(
                """async (url) => {
                    const r = await fetch(url, {credentials:'same-origin'});
                    const t = await r.text();
                    try { return {status:r.status, json:JSON.parse(t)}; }
                    catch(e) { return {status:r.status, textPrefix:t.slice(0,500)}; }
                }""", url)
            js = obj.get("json") or {}
            aw = js.get("aweme_list") or []
            pages_meta.append(_worklist_page_meta(pn, obj, js, aw))
            all_items.extend(aw)
            nxt = js.get("max_cursor") or js.get("cursor")
            if not aw or not js.get("has_more") or nxt in (None, "", cursor):
                break
            cursor = nxt
            await page.wait_for_timeout(1000)

    seen: set[Any] = set()
    items: list[dict[str, Any]] = []
    for a in all_items:
        n = _normalize_aweme(a)
        key = n.get("aweme_id") or id(a)
        if key in seen:
            continue
        seen.add(key)
        items.append(n)
    items.sort(key=lambda x: int(x.get("create_time") or 0), reverse=True)
    cutoff = (datetime.now(TZ) - timedelta(days=days)).date() if days else None
    rows = [n for n in items
            if cutoff is None or _safe_date(n.get("create_time")) >= cutoff] if cutoff else items

    captured = datetime.now(TZ).isoformat()
    items_c = [_aweme_canonical(n, account, captured) for n in items]
    rows_c = [_aweme_canonical(n, account, captured) for n in rows]

    raw, processed = output_dirs(ws, account, "douyin")
    stamp = _stamp()
    result = {
        "schema_version": schema.SCHEMA_VERSION,
        "account": account, "platform": "douyin",
        "source": "Douyin creator center /janus/douyin/creator/pc/work_list",
        "captured_at": captured,
        "range": {"days": days, "cutoff": cutoff.isoformat() if cutoff else None},
        "field_notes": {
            "duration_s": "Video duration in seconds; Douyin work_list duration is normalized from milliseconds when needed.",
            "metrics.shares": "Uses the work_list share/share_count value as the display share count.",
            "platform_fields.forward": "Raw forward/forward_count value when present; semantics are not used as display shares.",
        },
        "page_count": len(pages_meta), "item_count": len(items_c),
        "items": items_c, "selected_items": rows_c, "pages": pages_meta,
    }
    if not items_c:
        result["warning"] = "no works returned — storage state may be expired; re-run login/import-cookies"
        result["diagnostics"] = {
            "landing_on_login_page": _looks_like_login_page(landing_body),
            "likely_login_required": _worklist_likely_login_required(landing_body, pages_meta),
            "pages": pages_meta,
        }
    jp = raw / f"douyin-worklist-{days or 'all'}d-{stamp}.json"
    mp = processed / f"douyin-worklist-{days or 'all'}d-{stamp}.md"
    jp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"# {account} Douyin worklist ({days or 'all'} days)", "",
             f"Captured at: {captured}",
             f"Items: {len(items_c)}; selected: {len(rows_c)}", "",
             "| Time | Work ID | Title | Duration | Play | Like | Comment | Share | Collect |",
             "|---|---|---|---:|---:|---:|---:|---:|---:|"]
    for r in rows_c:
        m = r["metrics"]
        title = (r.get("title") or "").replace("|", "/").replace("\n", " ")[:80]
        pub = (r.get("published_at") or "")[:19].replace("T", " ")
        lines.append(f"| {pub} | {r.get('content_id', '')} | {title} | "
                     f"{_fmt(r.get('duration_s'))} | "
                     f"{_fmt(m.get('plays'))} | {_fmt(m.get('likes'))} | {_fmt(m.get('comments'))} | "
                     f"{_fmt(m.get('shares'))} | {_fmt(m.get('collects'))} |")
    mp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"ok": True, "json": str(jp), "markdown": str(mp),
            "items": len(items_c), "selected": len(rows_c)}


def _safe_date(ts: Any):
    try:
        return datetime.fromtimestamp(int(ts), TZ).date()
    except Exception:
        return datetime.fromtimestamp(0, TZ).date()


def _fmt(v: Any) -> str:
    if v in (None, ""):
        return ""
    try:
        if isinstance(v, float) and not v.is_integer():
            return f"{v:.3f}".rstrip("0").rstrip(".")
        return f"{int(v):,}"
    except Exception:
        return str(v)


# ── per-video detail metrics (作品分析: completion / watch / bounce) ───────
#
# Douyin's 作品分析 page exposes per-work early-retention metrics the basic
# work_list does not: average watch duration, 5-second completion, and 2-second
# bounce. Unlike fan-growth this is a clean JSON API (no DOM scraping), called
# from inside the page so the a-bogus/login context is the browser's.
#
# Reproduces the page's own sequence: GET involved_vertical for the account's
# primary_verticals, then POST item_analysis/{overview,item_performance}. Both the
# fixed genre set and the verticals are REQUIRED — an empty value makes the API
# return zero items (verified during discovery). Dates are YYYYMMDD.
#
# Honest limits: full 完播率, a progress retention curve, and per-video traffic
# source are NOT exposed by a clean creator-center web API for this surface — only
# the early-retention trio below is. See AGENTS.md.

_ITEM_ANALYSIS_BASE = "/janus/douyin/creator/data/item_analysis"
_ITEM_GENRES = [1, 2, 3, 4, 5, 8]  # content-genre enum; empty body → 0 items


def _yyyymmdd(d: "datetime") -> str:
    return d.strftime("%Y%m%d")


def _frac_pct(value: Any) -> float | None:
    """Douyin rates are 0..1 fractions → percent, 2dp (0.4066 → 40.66)."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return round(v * 100, 2)


def _round_or_none(value: Any, ndigits: int = 2) -> float | None:
    try:
        return round(float(value), ndigits)
    except (TypeError, ValueError):
        return None


def _client_split(block: Any, *, parse: bool) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if not isinstance(block, dict):
        return out
    for key, value in block.items():
        v = _parse_int(value) if parse else (value if isinstance(value, (int, float)) else None)
        if v is not None:
            out[key] = v
    return out


def _item_perf_row(item: dict[str, Any], account: str, captured: str) -> dict[str, Any]:
    """One 作品分析 item → canonical row with early-retention metrics. Pure → testable."""
    aweme_id = item.get("item_id")
    metrics = {
        "plays": _parse_int(item.get("play_count")),
        "avg_watch_duration_s": _round_or_none(item.get("average_play_duration")),
        "completion_rate_5s_pct": _frac_pct(item.get("completion_rate_5s")),
        "bounce_rate_2s_pct": _frac_pct(item.get("bounce_rate_2s")),
    }
    row = schema.video_row(
        platform="douyin", account=account, content_id=aweme_id,
        title=item.get("title"), published_at=item.get("publish_time") or None,
        captured_at=captured,
        source_url=f"https://www.douyin.com/video/{aweme_id}" if aweme_id else None,
        metrics=metrics)
    cover = _first_url(item.get("cover"))
    if cover:
        row["cover_url"] = cover
    detail = {
        "play_count_by_client": _client_split(item.get("play_count_per_client"), parse=True),
        "avg_watch_duration_s_by_client": _client_split(item.get("average_play_duration_per_client"), parse=False),
    }
    detail = {k: v for k, v in detail.items() if v}
    if detail:
        row["detail"] = detail
    return row


def _overview_block(ov: dict[str, Any]) -> dict[str, Any]:
    """Account-level 作品分析 overview → {key: {label, value, value_pct?}}."""
    out: dict[str, Any] = {}
    rate_keys = {"completion_rate_5s", "bounce_rate_2s", "cover_click_ratio"}
    for key, cell in (ov or {}).items():
        if not isinstance(cell, dict) or "metric_value" not in cell:
            continue
        entry = {"label": cell.get("metric_name"), "value": cell.get("metric_value")}
        if key in rate_keys:
            entry["value_pct"] = _frac_pct(cell.get("metric_value"))
        out[key] = entry
    return out


def item_analysis(*, ws: Path, account: str, state_path: Path, days: int,
                  chromium: str | None) -> dict[str, Any]:
    return asyncio.run(_item_analysis(ws, account, state_path, days, chromium))


async def _item_analysis(ws, account, state_path, days, chromium) -> dict[str, Any]:
    if not state_path.exists():
        raise CollectorError(f"missing Douyin storage state; run login/import-cookies first: {state_path}")
    end = datetime.now(TZ)
    start = end - timedelta(days=days)
    start_s, end_s = _yyyymmdd(start), _yyyymmdd(end)
    async_playwright = _import_playwright()
    async with async_playwright() as p, _browser(p, chromium) as browser:
        ctx = await browser.new_context(storage_state=str(state_path), **_CTX)
        page = await ctx.new_page()
        await page.goto("https://creator.douyin.com/creator-micro/data-center/content",
                        wait_until="domcontentloaded", timeout=60000)
        await _body_text_with_markers(
            page, ("作品分析", "数据中心", "扫码登录", "验证码登录", "登录/注册"), timeout_ms=10000)
        # Return raw response TEXT and parse in Python: a 19-digit item_id is a bare
        # JSON number, and the browser's JSON.parse would round it to float64
        # (…066994 → …067000). Python json keeps arbitrary-precision ints.
        obj = await page.evaluate(
            """async ({base, start, end, genres}) => {
                const iv = await (await fetch(`${base}/involved_vertical?start_date=${start}&end_date=${end}`,
                    {credentials:'same-origin'})).json();
                const pv = (iv && iv.primary_verticals) || [];
                const post = async (path, extra) => {
                    const r = await fetch(`${base}/${path}`, {method:'POST',
                        headers:{'content-type':'application/json'}, credentials:'same-origin',
                        body: JSON.stringify({start_date:start, end_date:end, genres, primary_verticals:pv, ...extra})});
                    return {status: r.status, text: await r.text()};
                };
                return {
                    primary_verticals: pv,
                    overview: await post('overview', {}),
                    item_performance: await post('item_performance', {metric_type:1}),
                };
            }""",
            {"base": _ITEM_ANALYSIS_BASE, "start": start_s, "end": end_s, "genres": _ITEM_GENRES})

    def _parse_resp(label: str, resp: dict[str, Any]) -> dict[str, Any]:
        status = resp.get("status", 0)
        text = resp.get("text") or ""
        try:
            js = json.loads(text) if text else {}
        except json.JSONDecodeError:
            raise CollectorError(
                f"Douyin item-analysis {label} returned non-JSON (status={status}); "
                f"login may be expired — re-run login/import-cookies. prefix={text[:200]!r}")
        err = _api_error(js)
        if status >= 400 or err:
            raise CollectorError(f"Douyin item-analysis {label} API error status={status} detail={err}")
        return js

    ov_js = _parse_resp("overview", obj.get("overview") or {})
    ip_js = _parse_resp("item_performance", obj.get("item_performance") or {})

    items = ip_js.get("items") or []
    captured = datetime.now(TZ).isoformat()
    rows = [_item_perf_row(it, account, captured) for it in items if isinstance(it, dict)]
    rows.sort(key=lambda r: r.get("published_at") or "", reverse=True)

    raw, processed = output_dirs(ws, account, "douyin")
    stamp = _stamp()
    result = {
        "schema_version": schema.SCHEMA_VERSION,
        "account": account, "platform": "douyin",
        "source": "Douyin creator center /janus/douyin/creator/data/item_analysis",
        "captured_at": captured,
        "range": {"start": start_s, "end": end_s, "days": days},
        "field_notes": {
            "metrics.avg_watch_duration_s": "条均播放时长 (average watch seconds per play).",
            "metrics.completion_rate_5s_pct": "5秒完播率 — early-retention proxy, percent.",
            "metrics.bounce_rate_2s_pct": "2秒跳出率 — early-exit rate, percent.",
            "coverage": "Batch overview of works published within [start, end] (5s完播/2s跳出/平均观看). "
                        "For one work's full 完播率, 流量来源, 进度曲线 and 搜索词, use `douyin video-detail --aweme-id`.",
        },
        "account_overview": _overview_block(ov_js),
        "item_count": len(rows),
        "items": rows,
    }
    if not rows:
        result["warning"] = ("no items returned — storage state may be expired, or no works were "
                             "published in range; re-run login/import-cookies and widen --days")
    jp = raw / f"douyin-item-analysis-{days}d-{stamp}.json"
    mp = processed / f"douyin-item-analysis-{days}d-{stamp}.md"
    jp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"# {account} Douyin 作品分析 ({days} days)", "",
             f"Range: {start_s} → {end_s}  ·  items: {len(rows)}", "",
             "| Published | Work ID | Title | Play | 平均观看(s) | 5s完播% | 2s跳出% |",
             "|---|---|---|---:|---:|---:|---:|"]
    for r in rows:
        m = r["metrics"]
        title = (r.get("title") or "").replace("|", "/").replace("\n", " ")[:60]
        pub = (r.get("published_at") or "")[:16]
        lines.append(f"| {pub} | {r.get('content_id', '')} | {title} | "
                     f"{_fmt(m.get('plays'))} | {_fmt(m.get('avg_watch_duration_s'))} | "
                     f"{_fmt(m.get('completion_rate_5s_pct'))} | {_fmt(m.get('bounce_rate_2s_pct'))} |")
    mp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"ok": True, "json": str(jp), "markdown": str(mp), "items": len(rows)}


# ── single-video detail (完播率 / 流量来源 / 进度 / 搜索词 / 同类对比) ──────
#
# The 作品分析 → 投稿列表 → 分析详情 page (work-detail/<aweme_id>) is where Douyin
# exposes, per work, what the batch 投稿分析 cannot: full 完播率, the per-video
# traffic-source split, drag-back/forward progress curves, the search terms that
# surfaced the work, and a same-tier peer comparison.
#
# These endpoints reject a raw same-origin fetch (Douyin signs them with header
# tokens its own interceptor adds), so — like `comments` — we navigate the real
# page, click the 流量分析/观众分析 tabs to trigger the calls, and INTERCEPT the
# responses. item_compare metric values are 0..1 fraction strings; `_pct_str`
# normalizes them to percent.

_DETAIL_TARGETS = {
    "compare": "/data/diagnose/item_compare",
    "source": "/data/item/play/source",
    "progress": "/bff/data/progress/analysis/v2",
    "search": "/data/item_analysis/search/keyword",
    "portrait": "/data/fans/item/portrait",
}

# Douyin traffic-source keys → human labels; unknown keys pass through verbatim.
_DY_SOURCE_LABELS = {
    "homepage_hot": "推荐(首页推荐)", "follow": "关注", "homepage": "个人主页",
    "search": "搜索", "message": "私信/分享", "familiar": "朋友/熟人",
    "nearby": "同城", "other": "其他",
}


def _pct_str(value: Any) -> float | None:
    """Douyin item_compare metric strings are 0..1 fractions → percent, 2dp."""
    try:
        return round(float(value) * 100, 2)
    except (TypeError, ValueError):
        return None


def _sec_str(value: Any) -> float | None:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _source_rows(play_source: Any) -> list[dict[str, Any]]:
    rows = []
    for x in play_source or []:
        if not isinstance(x, dict):
            continue
        key, pct = x.get("key"), _pct_str(x.get("value"))
        if key is None or pct is None:
            continue
        rows.append({"source_key": str(key),
                     "source_label": _DY_SOURCE_LABELS.get(str(key), str(key)),
                     "share_pct": pct})
    return sorted(rows, key=lambda r: -r["share_pct"])


def _progress_curve(points: Any) -> list[dict[str, Any]]:
    out = []
    for x in points or []:
        if not isinstance(x, dict):
            continue
        try:
            sec = float(x.get("key"))
        except (TypeError, ValueError):
            continue
        pct = _pct_str(x.get("value"))
        if pct is None:
            continue
        out.append({"second": sec, "pct": pct})
    return out


def _portrait_block(portrait: Any) -> dict[str, Any]:
    """Extract the readable slices of fans/item/portrait (ratios are 0..1)."""
    if not isinstance(portrait, dict):
        return {}

    def _ratio_list(node: Any, top: int | None = None) -> list[dict[str, Any]]:
        rows = (node or {}).get("ratio_list") if isinstance(node, dict) else None
        out = []
        for r in rows or []:
            if isinstance(r, dict) and r.get("key") not in (None, ""):
                out.append({"key": str(r["key"]), "pct": _pct_str(r.get("value"))})
        out = [r for r in out if r["pct"] is not None]
        out.sort(key=lambda r: -r["pct"])
        return out[:top] if top else out

    block = {
        "gender": _ratio_list(portrait.get("gender")),
        "age": _ratio_list(portrait.get("age")),
        "top_provinces": _ratio_list(portrait.get("province"), top=8),
        "city_level": _ratio_list(portrait.get("city_level")),
    }
    return {k: v for k, v in block.items() if v}


def _peer_ids(compare: dict[str, Any]) -> list[str]:
    """The same-tier works this video is benchmarked against. ``item_compare`` returns
    only their ids/descriptions (no metrics — those would need a separate fetch)."""
    ids = compare.get("compare_item_ids")
    if isinstance(ids, list) and ids:
        return [str(i) for i in ids if i not in (None, "")]
    return [str(it.get("id")) for it in (compare.get("compare_items") or [])
            if isinstance(it, dict) and it.get("id")]


def _dy_detail_row(*, account: str, captured: str, aweme_id: str, compare: dict[str, Any],
                   source: dict[str, Any], progress: dict[str, Any], search: dict[str, Any],
                   portrait: dict[str, Any]) -> dict[str, Any]:
    """Build one canonical single-video row + ``detail`` block. Pure → testable."""
    item = (compare or {}).get("item") or {}
    m = item.get("metrics") or {}
    peer_ids = _peer_ids(compare or {})
    metrics = {
        "plays": _parse_int(m.get("view_count")),
        "avg_watch_duration_s": _sec_str(m.get("avg_view_second")),
        "completion_rate_pct": _pct_str(m.get("completion_rate")),
        "completion_rate_5s_pct": _pct_str(m.get("completion_rate_5s")),
        "bounce_rate_2s_pct": _pct_str(m.get("bounce_rate_2s")),
        "avg_view_proportion_pct": _pct_str(m.get("avg_view_proportion")),
        "cover_click_rate_pct": _pct_str(m.get("cover_click_rate")),
        "follower_play_ratio_pct": _pct_str(m.get("fan_view_proportion")),
    }
    row = schema.video_row(
        platform="douyin", account=account, content_id=aweme_id or None,
        title=item.get("description") or None,
        published_at=_iso(item.get("create_time")), captured_at=captured,
        source_url=f"https://www.douyin.com/video/{aweme_id}" if aweme_id else None,
        metrics={k: v for k, v in metrics.items() if v is not None})

    engagement = {}
    for label, key in (("like", "like_rate"), ("comment", "comment_rate"),
                       ("share", "share_rate"), ("favorite", "favorite_rate"),
                       ("subscribe", "subscribe_rate"), ("unsubscribe", "unsubscribe_rate")):
        pct = _pct_str(m.get(key))
        if pct is not None:
            engagement[label] = pct

    detail: dict[str, Any] = {
        "traffic_source": _source_rows((source or {}).get("play_source")),
        "progress_analysis": {
            "drag_back_curve": _progress_curve((progress or {}).get("jump_backward")),
            "drag_forward_curve": _progress_curve((progress or {}).get("jump_forward")),
            "note": "Douyin exposes drag-back/forward distributions by playback second, "
                    "not a plain 'still-watching' retention curve.",
        },
        "search_keywords": [
            {"keyword": k.get("keyword"), "percent": k.get("percent")}
            for k in ((search or {}).get("show_from") or []) if isinstance(k, dict) and k.get("keyword")
        ],
        "engagement_rates_pct": engagement,
        "peer_comparison": {
            "peer_count": len(peer_ids),
            "peer_aweme_ids": peer_ids,
            "note": "same-tier works Douyin benchmarks this one against; their metrics "
                    "are not in this response — fetch each with douyin video-detail.",
        },
        "audience": _portrait_block(portrait),
    }

    def _clean(obj: Any) -> Any:
        if isinstance(obj, dict):
            cleaned = {k: _clean(v) for k, v in obj.items()}
            return {k: v for k, v in cleaned.items() if v not in (None, {}, [])}
        return obj

    row["detail"] = _clean(detail)
    return row


def video_detail(*, ws: Path, account: str, state_path: Path, aweme_id: str,
                 chromium: str | None) -> dict[str, Any]:
    return asyncio.run(_video_detail(ws, account, state_path, aweme_id, chromium))


async def _video_detail(ws, account, state_path, aweme_id, chromium):
    if not state_path.exists():
        raise CollectorError(f"missing Douyin storage state; run login/import-cookies first: {state_path}")
    grabbed: dict[str, Any] = {}
    async_playwright = _import_playwright()
    landing_body = ""
    async with async_playwright() as p, _browser(p, chromium) as browser:
        ctx = await browser.new_context(storage_state=str(state_path), **_CTX)
        page = await ctx.new_page()

        async def on_response(resp):
            url = resp.url
            for name, frag in _DETAIL_TARGETS.items():
                if frag in url and name not in grabbed:
                    try:
                        grabbed[name] = await resp.json()
                    except Exception:
                        pass

        page.on("response", on_response)
        await page.goto(
            f"https://creator.douyin.com/creator-micro/work-management/work-detail/{aweme_id}",
            wait_until="domcontentloaded", timeout=60000)
        landing_body = await _body_text_with_markers(
            page, ("流量分析", "观众分析", "完播率", "扫码登录", "验证码登录", "登录/注册"), timeout_ms=12000)
        # The default 数据概览 view fires item_compare on its own — wait for it BEFORE
        # touching the tabs, or clicking 流量分析 navigates away before it lands.
        for _ in range(15):
            if "compare" in grabbed:
                break
            await page.wait_for_timeout(1000)
        # The 流量分析/观众分析 tabs fire the rest. Click each (exact text, scrolled into
        # view) and poll until its endpoints actually land — a bare click + fixed sleep
        # silently missed them when the tab rendered slowly.
        for label, needed in (("流量分析", ("source", "progress", "search")),
                              ("观众分析", ("portrait",))):
            try:
                el = page.get_by_text(label, exact=True).first
                if not await el.count():
                    continue
                await el.scroll_into_view_if_needed(timeout=3000)
                await el.click(timeout=4000)
            except Exception:
                continue
            for _ in range(8):
                if all(n in grabbed for n in needed):
                    break
                await page.wait_for_timeout(1000)

    if "compare" not in grabbed:
        if _looks_like_login_page(landing_body):
            raise CollectorError(
                "Douyin single-video detail landed on a login page — storage state expired; "
                "re-run login/import-cookies.")
        raise CollectorError(
            "could not load Douyin single-video analysis (item_compare) — wrong aweme_id, the "
            "work is not yours, or Douyin changed work-detail. Update to the latest release or "
            "report upstream (see AGENTS.md 'Staying current').")
    # item_compare returns 200 with an empty item for a bogus/foreign aweme_id — guard
    # against silently emitting a metric-less row.
    _item_metrics = ((grabbed["compare"].get("item") or {}).get("metrics")) or {}
    if not _item_metrics or _item_metrics.get("view_count") in (None, ""):
        raise CollectorError(
            f"Douyin returned no metrics for aweme_id {aweme_id} — wrong id, or the work is not "
            "yours. Pass an aweme_id from `douyin worklist`/`item-analysis`.")

    captured = datetime.now(TZ).isoformat()
    row = _dy_detail_row(account=account, captured=captured, aweme_id=str(aweme_id),
                         compare=grabbed.get("compare") or {}, source=grabbed.get("source") or {},
                         progress=grabbed.get("progress") or {}, search=grabbed.get("search") or {},
                         portrait=grabbed.get("portrait") or {})
    result = {
        "schema_version": schema.SCHEMA_VERSION,
        "account": account, "platform": "douyin",
        "source": "Douyin creator center 作品分析 → 分析详情 (work-detail) APIs",
        "captured_at": captured,
        "aweme_id": str(aweme_id),
        "endpoints_seen": sorted(grabbed.keys()),
        "field_notes": {
            "metrics.completion_rate_pct": "完播率 — share who watched to the end, percent.",
            "metrics.avg_watch_duration_s": "平均播放时长 (avg_view_second), seconds.",
            "metrics.follower_play_ratio_pct": "粉丝播放占比 (fan_view_proportion), percent.",
            "detail.traffic_source": "播放来源 split (推荐/关注/搜索/个人主页/…), percent shares.",
            "detail.progress_analysis": "Drag-back/forward distribution by playback second (not a still-watching curve).",
        },
        "video": row,
    }
    raw, processed = output_dirs(ws, account, "douyin")
    stamp = _stamp()
    jp = raw / f"douyin-video-detail-{aweme_id}-{stamp}.json"
    mp = processed / f"douyin-video-detail-{aweme_id}-{stamp}.md"
    jp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    mp.write_text(_render_dy_detail_md(row), encoding="utf-8")
    m = row["metrics"]
    return {"ok": True, "json": str(jp), "markdown": str(mp), "aweme_id": str(aweme_id),
            "completion_rate_pct": m.get("completion_rate_pct"),
            "avg_watch_duration_s": m.get("avg_watch_duration_s"),
            "traffic_sources": len(row.get("detail", {}).get("traffic_source") or [])}


def _render_dy_detail_md(row: dict[str, Any]) -> str:
    m = row["metrics"]
    d = row.get("detail") or {}
    lines = [
        f"# 单稿数据详情：{row.get('title') or row.get('content_id')}",
        "",
        f"- aweme_id: `{row.get('content_id')}`",
        f"- 播放：{_fmt(m.get('plays'))}",
        f"- 完播率：{m.get('completion_rate_pct', '—')}%",
        f"- 平均观看时长：{m.get('avg_watch_duration_s', '—')} 秒",
        f"- 5s完播率 / 2s跳出率：{m.get('completion_rate_5s_pct', '—')}% / {m.get('bounce_rate_2s_pct', '—')}%",
        f"- 封面点击率：{m.get('cover_click_rate_pct', '—')}%",
        f"- 粉丝播放占比：{m.get('follower_play_ratio_pct', '—')}%",
        "",
        "## 流量来源",
        "",
        "| 来源 | 占比 |",
        "|---|---:|",
    ]
    for s in (d.get("traffic_source") or []):
        lines.append(f"| {s['source_label']} | {s['share_pct']}% |")
    kws = d.get("search_keywords") or []
    if kws:
        lines += ["", "## 搜索来源关键词", "",
                  "、".join(f"{k['keyword']}({k['percent']}%)" for k in kws)]
    return "\n".join(lines) + "\n"


# ── per-video fan growth (粉丝增量) — DOM only ─────────────────────────────

_FAN_COL = "粉丝增量"

# JS lives as a module constant so the row-grouping logic is reviewable, not
# buried in an f-string. It returns rows as cell-text arrays, grouped by the
# top coordinate of each cell (Douyin renders a flex/grid pseudo-table).
_EXTRACT_TABLE_JS = """() => {
    const cells = document.querySelectorAll('td,th');
    const rows = []; let cur = []; let lastTop = null;
    cells.forEach(c => {
        const top = Math.round(c.getBoundingClientRect().top);
        if (lastTop !== null && top !== lastTop && cur.length) { rows.push(cur); cur = []; }
        cur.push((c.innerText || '').trim());
        lastTop = top;
    });
    if (cur.length) rows.push(cur);
    return rows;
}"""


def _parse_fan_table(table: list) -> list:
    """Locate the 粉丝增量 column by header TEXT (survives reordering) and parse the
    data rows. Returns [] if no 粉丝增量 header is present in this table snapshot.
    """
    header_idx = next((i for i, row in enumerate(table)
                       if any(_FAN_COL in (c or "") for c in row)), None)
    if header_idx is None:
        return []
    header = table[header_idx]
    fan_col = next(i for i, c in enumerate(header) if _FAN_COL in (c or ""))
    rows = []
    for row in table[header_idx + 1:]:
        if len(row) <= fan_col or not row[0]:
            continue
        first = row[0].split("\n")
        rows.append({
            "title": first[0],
            "published": first[1] if len(first) > 1 else "",
            "fan_growth_raw": row[fan_col],
            "fan_growth": _parse_int(row[fan_col]),
        })
    return rows


def _fan_growth_join_key(title: str | None, published: str | None) -> str | None:
    if not title and not published:
        return None
    raw = f"{(title or '').strip()}\n{(published or '').strip()}"
    return "title-published:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _fan_growth_canonical(r: dict[str, Any], account: str, captured_at: str) -> dict[str, Any]:
    row = schema.video_row(
        platform="douyin", account=account, content_id=None,
        title=r["title"], published_at=r["published"] or None, captured_at=captured_at,
        metrics={"fans": r["fan_growth"]})
    join_key = _fan_growth_join_key(row.get("title"), row.get("published_at"))
    if join_key:
        row["join_key"] = join_key
    return row


def fan_growth(*, ws: Path, account: str, state_path: Path, chromium: str | None,
               max_scroll: int = 40) -> dict[str, Any]:
    return asyncio.run(_fan_growth(ws, account, state_path, chromium, max_scroll))


async def _fan_growth(ws, account, state_path, chromium, max_scroll) -> dict[str, Any]:
    if not state_path.exists():
        raise CollectorError(f"missing Douyin storage state; run login/import-cookies first: {state_path}")
    async_playwright = _import_playwright()
    async with async_playwright() as p, _browser(p, chromium) as browser:
        ctx = await browser.new_context(storage_state=str(state_path), **_CTX)
        page = await ctx.new_page()
        await page.goto("https://creator.douyin.com/creator-micro/data-center/content",
                        wait_until="domcontentloaded", timeout=60000)
        try:
            await page.locator("text=投稿列表").first.click(timeout=8000)
        except Exception as exc:
            raise CollectorError(
                "could not find the 投稿列表 tab — either not logged in, or Douyin changed "
                "data-center. If logged in, update to the latest release / report upstream "
                "(see AGENTS.md 'Staying current')."
            ) from exc
        await _wait_for_selector_or_short_fallback(page, "td, th", timeout_ms=10000)

        # 投稿列表 has NO pager — it lazy-loads rows on WINDOW scroll, bounded by its
        # 发布时间 filter. Scroll until the rendered cell count stops growing, then
        # extract the whole table once. (Older history needs widening that date
        # filter, which this command does not drive — see the README note.)
        prev, scrolls = -1, 0
        for _ in range(max_scroll):
            count = await page.evaluate("() => document.querySelectorAll('td,th').length")
            if count == prev:
                break
            prev = count
            await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
            await page.mouse.wheel(0, 3000)
            await page.wait_for_timeout(1500)
            scrolls += 1
        table = await page.evaluate(_EXTRACT_TABLE_JS)

    parsed = _parse_fan_table(table)
    if not parsed:
        raise CollectorError(
            f"'{_FAN_COL}' column not found in 投稿列表 — Douyin changed the table. Update to the "
            "latest release; if already current, report upstream (see AGENTS.md 'Staying current')."
        )

    captured = datetime.now(TZ).isoformat()
    # No aweme_id in the 投稿列表 DOM → content_id is null; join by (title, published).
    rows = [_fan_growth_canonical(r, account, captured) for r in parsed]

    raw, processed = output_dirs(ws, account, "douyin")
    stamp = _stamp()
    result = {
        "schema_version": schema.SCHEMA_VERSION,
        "account": account, "platform": "douyin", "metric": "fan_growth (粉丝增量)",
        "source": "Douyin creator data-center 投稿列表 DOM",
        "captured_at": captured,
        "scroll_rounds": scrolls, "row_count": len(rows), "rows": rows,
        "field_notes": {
            "join_key": "Fallback key for fan-growth rows when aweme_id is unavailable: sha1(title + newline + published_at), prefixed with title-published:. Stable for unchanged DOM text; can change if title or publish-time text changes.",
        },
        "note": "bounded by the 投稿列表 发布时间 filter; widen it for older history",
    }
    jp = raw / f"douyin-fan-growth-{stamp}.json"
    mp = processed / f"douyin-fan-growth-{stamp}.md"
    jp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"# {account} Douyin per-video fan growth", "",
             f"Captured at: {captured}  ·  rows: {len(rows)}", "",
             "| Published | Title | 粉丝增量 |", "|---|---|---:|"]
    for r in rows:
        title = (r["title"] or "").replace("|", "/")[:80]
        lines.append(f"| {r['published_at'] or ''} | {title} | {_fmt(r['metrics'].get('fans'))} |")
    mp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"ok": True, "json": str(jp), "markdown": str(mp), "rows": len(rows)}


def _parse_int(s: Any) -> int | None:
    if s in (None, ""):
        return None
    m = re.search(r"-?[\d,]+", str(s))
    if not m:
        return None
    try:
        return int(m.group(0).replace(",", ""))
    except ValueError:
        return None


# ── comments ─────────────────────────────────────────────────────────────

def _comments_no_api_diagnostics(page_body: str, pages_seen: int) -> dict[str, Any]:
    return {
        "api_pages_intercepted": pages_seen,
        "comment_api_seen": pages_seen > 0,
        "landing_on_login_page": _looks_like_login_page(page_body),
    }


def comments(*, ws: Path, account: str, aweme_id: str, state_path: Path, max_pages: int,
             chromium: str | None) -> dict[str, Any]:
    return asyncio.run(_comments(ws, account, aweme_id, state_path, max_pages, chromium))


async def _comments(ws, account, aweme_id, state_path, max_pages, chromium) -> dict[str, Any]:
    if not state_path.exists():
        raise CollectorError(f"missing Douyin storage state; run import-cookies first: {state_path}")
    async_playwright = _import_playwright()
    collected: dict[Any, dict[str, Any]] = {}
    pages_seen = 0
    landing_body = ""
    async with async_playwright() as p, _browser(p, chromium) as browser:
        ctx = await browser.new_context(storage_state=str(state_path), **_CTX)
        page = await ctx.new_page()

        async def on_response(resp):
            nonlocal pages_seen
            if "/aweme/v1/web/comment/list" not in resp.url:
                return
            try:
                body = await resp.json()
            except Exception:
                return
            pages_seen += 1
            for cm in (body.get("comments") or []):
                cid = cm.get("cid")
                if cid and cid not in collected:
                    user = cm.get("user") or {}
                    collected[cid] = {
                        "cid": cid, "text": cm.get("text"),
                        "create_time": cm.get("create_time"),
                        "digg_count": cm.get("digg_count"),
                        "reply_comment_total": cm.get("reply_comment_total"),
                        "user_nickname": user.get("nickname"),
                        "ip_label": cm.get("ip_label"),
                    }

        page.on("response", on_response)
        await page.goto(f"https://www.douyin.com/video/{aweme_id}",
                        wait_until="domcontentloaded", timeout=60000)
        landing_body = await _body_text(page, timeout_ms=10000)
        # Douyin's comment-panel class names rotate; mouse-wheel + scrolling the
        # tallest scrollable element survives DOM churn better than CSS selectors.
        await page.mouse.move(1100, 500)
        last, stable = 0, 0
        for _ in range(max_pages * 3):
            await page.mouse.wheel(0, 1500)
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
            if len(collected) == last:
                stable += 1
                if stable >= 4:
                    break
            else:
                stable, last = 0, len(collected)

    raw, processed = output_dirs(ws, account, "douyin")
    stamp = _stamp()
    result = {
        "account": account, "platform": "douyin", "aweme_id": aweme_id,
        "collected_at": datetime.now(TZ).isoformat(),
        "comment_count": len(collected), "api_pages_intercepted": pages_seen,
        "comments": sorted(collected.values(), key=lambda c: -(c.get("digg_count") or 0)),
    }
    if pages_seen == 0:
        result["warning"] = (
            "no Douyin comment API responses were intercepted — login may be expired, "
            "the video may be unavailable, or Douyin changed the comment loading flow"
        )
        result["diagnostics"] = _comments_no_api_diagnostics(landing_body, pages_seen)
    jp = raw / f"douyin-comments-{aweme_id}-{stamp}.json"
    jp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {"ok": True, "json": str(jp), "aweme_id": aweme_id, "comments": len(collected)}
    if pages_seen == 0:
        summary["warning"] = result["warning"]
    return summary
