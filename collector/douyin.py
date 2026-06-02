"""Douyin: Playwright-driven creator-center and public-data collection.

Why a browser is unavoidable here:
  * login state + the per-request ``a-bogus`` signature are produced by Douyin's
    own JS — replicating them outside the browser is fragile, so work_list and
    comment APIs are fired *from inside the page* (``page.evaluate(fetch)`` /
    response interception).
  * per-video fan growth (粉丝增量) has no API at all — it exists only in the DOM
    of the 投稿列表 table, so we scrape it.

Commands: check-cookies, import-cookies, worklist, fan-growth, comments.
"""
from __future__ import annotations

import asyncio
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


def _aweme_canonical(n: dict[str, Any], account: str, captured_at: str) -> dict[str, Any]:
    """Map an internal normalized aweme to a canonical video row."""
    return schema.video_row(
        platform="douyin", account=account, content_id=n.get("aweme_id"),
        title=n.get("title"), published_at=_iso(n.get("create_time")),
        captured_at=captured_at, source_url=n.get("url"),
        metrics={
            "plays": n.get("play"), "likes": n.get("like"),
            "comments": n.get("comment"), "shares": n.get("share"),
            "collects": n.get("collect"),
        })


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


def _normalize_aweme(a: dict[str, Any]) -> dict[str, Any]:
    stat = a.get("Statistics") or a.get("statistics") or {}
    item = {
        "aweme_id": _pick(a, "AwemeId", "aweme_id", "item_id", "id"),
        "title": _pick(a, "Desc", "desc", "Title", "title") or "",
        "create_time": _pick(a, "CreateTime", "create_time"),
    }
    item["create_time_str"] = _ts_to_str(item["create_time"])
    for out, names in {
        "play": ["PlayCnt", "play_count", "play", "view_count"],
        "like": ["DiggCnt", "digg_count", "like_count"],
        "comment": ["CommentCnt", "comment_count"],
        "share": ["ShareCnt", "share_count"],
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
             "| Time | Work ID | Title | Play | Like | Comment | Share | Collect |",
             "|---|---|---|---:|---:|---:|---:|---:|"]
    for r in rows_c:
        m = r["metrics"]
        title = (r.get("title") or "").replace("|", "/").replace("\n", " ")[:80]
        pub = (r.get("published_at") or "")[:19].replace("T", " ")
        lines.append(f"| {pub} | {r.get('content_id', '')} | {title} | "
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
        return f"{int(v):,}"
    except Exception:
        return str(v)


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
    rows = [schema.video_row(
        platform="douyin", account=account, content_id=None,
        title=r["title"], published_at=r["published"] or None, captured_at=captured,
        metrics={"fans": r["fan_growth"]}) for r in parsed]

    raw, processed = output_dirs(ws, account, "douyin")
    stamp = _stamp()
    result = {
        "schema_version": schema.SCHEMA_VERSION,
        "account": account, "platform": "douyin", "metric": "fan_growth (粉丝增量)",
        "source": "Douyin creator data-center 投稿列表 DOM",
        "captured_at": captured,
        "scroll_rounds": scrolls, "row_count": len(rows), "rows": rows,
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
