"""Bilibili: plain-HTTPS creator-center and public-data collection.

Why no browser here: creator cookies sent as a ``Cookie`` header are enough for
the creator-center and comment APIs, so Bilibili stays a lightweight httpx path.
``SESSDATA``/``bili_jct`` prove the login session; ``buvid3`` is preserved when
available but older valid credentials may not have it. Douyin can't — see ``douyin.py``.

Commands: probe, summary, fan-source, comments, danmaku.
"""
from __future__ import annotations

import asyncio
import json
import re
import statistics
import sys
import time
import urllib.parse
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from . import schema
from .paths import TZ, CollectorError, output_dirs

REQUIRED_FIELDS = ("SESSDATA", "bili_jct")
OPTIONAL_COOKIE_FIELDS = ("buvid3", "DedeUserID", "DedeUserID__ckMd5", "sid")
LOGIN_COOKIE_FIELDS = REQUIRED_FIELDS + ("buvid3",)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


# ── credentials ──────────────────────────────────────────────────────────

def _normalize_credentials(data: Any) -> dict[str, str]:
    if isinstance(data, list):
        return {
            str(c["name"]): str(c["value"])
            for c in data
            if isinstance(c, dict) and c.get("name") and c.get("value")
        }
    if isinstance(data, dict):
        cookies = data.get("cookies")
        if isinstance(cookies, list):
            return _normalize_credentials(cookies)
        return {str(k): str(v) for k, v in data.items() if v}
    raise CollectorError("Bilibili credential must be a JSON object or Cookie-Editor list")


def load_credentials(path: Path, *, required: tuple[str, ...] = REQUIRED_FIELDS) -> dict[str, str]:
    if not path.exists():
        raise CollectorError(f"missing Bilibili credential file: {path}")
    creds = _normalize_credentials(json.loads(path.read_text(encoding="utf-8")))
    missing = [k for k in required if not creds.get(k)]
    if missing:
        raise CollectorError(f"Bilibili credential missing fields {missing}; path={path}")
    keep = required + tuple(k for k in OPTIONAL_COOKIE_FIELDS if k not in required)
    return {k: creds[k] for k in keep if creds.get(k)}


def cookie_header(creds: dict[str, str]) -> str:
    return "; ".join(f"{k}={v}" for k, v in creds.items())


def _client(cookie: str | None = None, referer: str = "https://www.bilibili.com/") -> httpx.Client:
    headers = {"User-Agent": _UA, "Referer": referer, "Accept": "application/json, text/plain, */*"}
    if cookie:
        headers["Cookie"] = cookie
    return httpx.Client(headers=headers, timeout=30, follow_redirects=True)


# 429 + 5xx are worth a retry; other 4xx and the JSON-level risk codes are not.
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


def _get_json(client: httpx.Client, url: str, params: dict[str, Any] | None = None,
              *, retries: int = 3, backoff_s: float = 0.8) -> dict[str, Any]:
    """GET + parse Bilibili JSON, with bounded retry on *transient* failures only.

    Retries network errors (timeout / reset) and 429/5xx with exponential backoff.
    Never retries other 4xx, and never retries a JSON ``code != 0`` — those are
    risk-control / business errors (``-412/-799/-352/-403`` …) where hammering only
    makes things worse, so they fail loud immediately.
    """
    last = "no attempt made"
    for attempt in range(retries + 1):
        try:
            resp = client.get(url, params=params or {})
        except httpx.TransportError as exc:  # timeout, connection reset, DNS — transient
            last = f"network error: {exc}"
        else:
            if resp.status_code in _RETRY_STATUS:
                last = f"HTTP {resp.status_code}"
            elif resp.status_code >= 400:        # other 4xx (412/403/404…): do NOT hammer
                raise CollectorError(f"Bilibili HTTP {resp.status_code} url={resp.url} — not retrying")
            else:
                obj = resp.json()
                code = obj.get("code")
                if code not in (0, None):
                    raise CollectorError(
                        f"Bilibili API error code={code} message={obj.get('message')!r} url={resp.url}"
                    )
                return obj
        if attempt < retries:
            time.sleep(backoff_s * (2 ** attempt))
    raise CollectorError(f"Bilibili request to {url} failed after {retries + 1} attempts ({last})")


def _stamp() -> str:
    return datetime.now(TZ).strftime("%Y%m%d-%H%M%S")


def _date_from_epoch(ts: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(ts), TZ)
    except Exception:
        return None


def _stat_int(*sources: dict[str, Any], names: tuple[str, ...], default: int = 0) -> int:
    for source in sources:
        if not isinstance(source, dict):
            continue
        for name in names:
            value = source.get(name)
            if value not in (None, ""):
                try:
                    return int(value)
                except Exception:
                    return default
    return default


def _pick(obj: dict[str, Any], *names: str) -> Any:
    for name in names:
        if isinstance(obj, dict) and obj.get(name) not in (None, ""):
            return obj[name]
    return None


def _duration_seconds(value: Any) -> float | None:
    if isinstance(value, str) and ":" in value:
        try:
            parts = [int(p) for p in value.split(":")]
        except Exception:
            return None
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        return None
    try:
        duration = float(value)
    except Exception:
        return None
    if duration <= 0:
        return None
    return int(duration) if duration.is_integer() else round(duration, 3)


def _bilibili_tags(client: httpx.Client, bvid: str | None) -> list[str]:
    if not bvid:
        return []
    try:
        obj = _get_json(
            client,
            "https://api.bilibili.com/x/tag/archive/tags",
            {"bvid": bvid},
            retries=1,
            backoff_s=0.2,
        )
    except CollectorError:
        return []
    tags = obj.get("data") or []
    if not isinstance(tags, list):
        return []
    out = []
    for tag in tags:
        if isinstance(tag, dict):
            name = tag.get("tag_name") or tag.get("name")
            if name:
                out.append(str(name))
    return out


def _bilibili_view_metadata(client: httpx.Client, bvid: str | None) -> dict[str, Any]:
    if not bvid:
        return {}
    try:
        return _get_json(
            client,
            "https://api.bilibili.com/x/web-interface/view",
            {"bvid": bvid},
            retries=1,
            backoff_s=0.2,
        ).get("data") or {}
    except CollectorError:
        return {}


def _bilibili_video_row_extra(client: httpx.Client, item: dict[str, Any]) -> dict[str, Any]:
    bvid = str(item.get("bvid") or "")
    view = _bilibili_view_metadata(client, bvid)
    duration = _duration_seconds(_pick(item, "duration", "length") or view.get("duration"))
    copyright_value = _pick(item, "copyright") or view.get("copyright")
    extra = {
        "duration_s": duration,
        "cover_url": _pick(item, "pic", "cover", "cover_url") or view.get("pic"),
        "category": _pick(item, "typename", "tname", "type_name") or view.get("tname"),
        "category_id": _pick(item, "typeid", "type_id") or view.get("tid"),
        "tags": _bilibili_tags(client, bvid),
        "status": _pick(item, "state", "status", "pub_state"),
        "audit_status": _pick(item, "audit_status", "review_status", "reject_reason"),
        "copyright": copyright_value,
    }
    if str(copyright_value) in ("1", "2"):
        extra["is_original"] = str(copyright_value) == "1"
    return {k: v for k, v in extra.items() if v not in (None, "", [])}


# ── probe ────────────────────────────────────────────────────────────────

def probe(*, ws: Path, account: str, credential_path: Path) -> dict[str, Any]:
    creds = load_credentials(credential_path)
    with _client(cookie_header(creds)) as c:
        data = _get_json(c, "https://api.bilibili.com/x/web-interface/nav").get("data") or {}
    if not data.get("isLogin"):
        raise CollectorError(
            "Bilibili cookie invalid or expired; re-export SESSDATA/bili_jct."
        )
    return {
        "ok": True,
        "isLogin": True,
        "mid": data.get("mid"),
        "uname": data.get("uname"),
        "level": (data.get("level_info") or {}).get("current_level"),
    }


# ── QR login (headed browser; B站 renders its own QR on passport page) ────

def login(*, ws: Path, account: str, credential_path: Path, chromium: str | None,
          timeout_s: int = 180) -> dict[str, Any]:
    return asyncio.run(_login_async(credential_path, chromium, timeout_s))


async def _login_async(credential_path: Path, chromium: str | None, timeout_s: int) -> dict[str, Any]:
    """Open a headed browser to passport.bilibili.com; the user scans the B站 QR.
    Poll for SESSDATA+bili_jct, ensure buvid3 (a device cookie set on first visit),
    then write the three creator cookies to the credential file. Never echoes values.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover - env dependent
        raise CollectorError("playwright not installed — pip install playwright") from exc
    from .browser import BUNDLED_HINT, launch_kwargs

    credential_path.parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=False, **launch_kwargs(chromium))
        except Exception as exc:
            raise CollectorError(f"{exc}\n\n{BUNDLED_HINT}") from exc
        try:
            ctx = await browser.new_context()
            page = await ctx.new_page()
            await page.goto("https://passport.bilibili.com/login", wait_until="domcontentloaded", timeout=60000)
            print("B站：浏览器窗口已打开，请用哔哩哔哩 App 扫码登录…", file=sys.stderr)
            creds: dict[str, str] = {}
            waited = 0
            while waited < timeout_s * 1000:
                jar = {c["name"]: c["value"] for c in await ctx.cookies()}
                if jar.get("SESSDATA") and jar.get("bili_jct"):
                    creds = jar
                    break
                await page.wait_for_timeout(2000)
                waited += 2000
            if not creds:
                raise CollectorError(
                    "Bilibili QR login timed out (no SESSDATA). Retry, or fall back to a "
                    "local credential file."
                )
            if not creds.get("buvid3"):
                await page.goto("https://www.bilibili.com/", wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(2000)
                creds = {c["name"]: c["value"] for c in await ctx.cookies()}
            mid = creds.get("DedeUserID")
        finally:
            await browser.close()

    out = {k: creds.get(k, "") for k in LOGIN_COOKIE_FIELDS}
    if creds.get("DedeUserID"):
        out["DedeUserID"] = creds["DedeUserID"]  # platform uid — handy for consumers, not required
    missing = [k for k in LOGIN_COOKIE_FIELDS if not out[k]]
    if missing:
        raise CollectorError(f"login succeeded but missing cookies {missing} — try again")
    credential_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        credential_path.chmod(0o600)
    except OSError:
        pass
    return {"ok": True, "credential": str(credential_path), "mid": mid, "method": "qr-login"}


# ── creator-center summary (fan trend + per-video stats) ─────────────────

def _archive_compare_by_bvid(client: httpx.Client, *, size: int = 50) -> dict[str, dict[str, Any]]:
    obj = _get_json(
        client,
        "https://member.bilibili.com/x/web/data/archive_diagnose/compare",
        {"size": size},
    )
    items = (obj.get("data") or {}).get("list") or []
    return {str(it["bvid"]): it for it in items if isinstance(it, dict) and it.get("bvid")}


def summary(*, ws: Path, account: str, credential_path: Path, days: int) -> dict[str, Any]:
    creds = load_credentials(credential_path)
    cookie = cookie_header(creds)
    referer = "https://member.bilibili.com/york/data-center-web?tmid=&bvid=&tab="
    with _client(cookie, referer) as c:
        # fail loud before doing real work
        nav = _get_json(c, "https://api.bilibili.com/x/web-interface/nav").get("data") or {}
        if not nav.get("isLogin"):
            raise CollectorError(
                "Bilibili cookie invalid or expired; re-export SESSDATA/bili_jct."
            )

        fan_obj = _get_json(
            c,
            "https://member.bilibili.com/x/web/data/v2/overview/stat/graph",
            {"period": 1, "s_locale": "zh_CN", "type": "fan", "tmid": "", "t": int(time.time() * 1000)},
        )
        trend = (fan_obj.get("data") or {}).get("tendency") or []
        trend_rows = [(x, _date_from_epoch(x.get("date_key"))) for x in trend if isinstance(x, dict)]
        trend_rows = [(x, dt) for x, dt in trend_rows if dt is not None]
        if not trend_rows:
            raise CollectorError("no Bilibili fan trend returned (cookie may lack creator access)")
        latest = max(dt.date() for _, dt in trend_rows)
        start = latest - _days(days - 1)
        captured = datetime.now(TZ).isoformat()
        fan_rows = sorted(
            (
                schema.fan_trend_row(
                    platform="bilibili", account=account,
                    date=dt.date().isoformat(),
                    fan_inc=int(x.get("total_inc") or 0), captured_at=captured,
                )
                for x, dt in trend_rows
                if start <= dt.date() <= latest
            ),
            key=lambda r: r["date"],
        )

        videos: list[dict[str, Any]] = []
        compare_by_bvid = _archive_compare_by_bvid(c)
        for pn in range(1, 50):
            obj = _get_json(
                c,
                "https://member.bilibili.com/x/web/data/archive/index",
                {"pn": pn, "ps": 20, "scene": "archive", "order": 0, "tmid": "", "t": int(time.time() * 1000)},
            )
            items = (obj.get("data") or {}).get("list") or []
            if not items:
                break
            for it in items:
                pubtime = it.get("pubtime")
                pub = _date_from_epoch(pubtime)
                if pub is None:
                    continue
                if start <= pub.date() <= latest:
                    stat = it.get("real_stat") or it.get("stat") or {}
                    compare_stat = (compare_by_bvid.get(str(it.get("bvid"))) or {}).get("stat") or {}
                    row = schema.video_row(
                        platform="bilibili", account=account, content_id=it.get("bvid"),
                        title=it.get("title"), published_at=pub.isoformat(), captured_at=captured,
                        source_url=f"https://www.bilibili.com/video/{it.get('bvid')}",
                        metrics={
                            "plays": _stat_int(stat, compare_stat, names=("play",)),
                            "likes": _stat_int(stat, compare_stat, names=("likes", "like")),
                            "comments": _stat_int(stat, compare_stat, names=("reply",)),
                            "coins": _stat_int(compare_stat, stat, names=("coin",)),
                            "fans": _stat_int(stat, compare_stat, names=("fans",)),
                            "collects": _stat_int(compare_stat, stat, names=("fav", "favorite")),
                            "shares": _stat_int(compare_stat, stat, names=("share",)),
                            "danmaku": _stat_int(compare_stat, stat, names=("dm",)),
                            "full_play_ratio": stat.get("full_play_ratio"),
                        })
                    row.update(_bilibili_video_row_extra(c, it))
                    videos.append(row)
            last_pubtime = items[-1].get("pubtime")
            last_pub = _date_from_epoch(last_pubtime)
            if last_pub and last_pub.date() < start:
                break
    videos.sort(key=lambda r: r["published_at"] or "", reverse=True)

    result = {
        "schema_version": schema.SCHEMA_VERSION,
        "account": account,
        "platform": "bilibili",
        "source": "Bilibili creator-center APIs",
        "range": {"start": start.isoformat(), "end": latest.isoformat(), "days": days},
        "captured_at": captured,
        "field_notes": {
            "duration_s": "Video duration in seconds, from creator archive data or public view metadata.",
            "cover_url": "Cover image URL from creator archive data or public view metadata.",
            "category": "Bilibili partition/category name when available.",
            "tags": "Public archive tags when the tag endpoint returns them; missing tags mean unavailable, not necessarily untagged.",
            "copyright": "Bilibili copyright value when available; 1 is treated as original and 2 as repost-like.",
        },
        "fan_total": sum(r["fan_inc"] for r in fan_rows),
        "fan_trend": fan_rows,
        "videos": videos,
    }
    raw, processed = output_dirs(ws, account, "bilibili")
    stamp = _stamp()
    jp = raw / f"bilibili-creator-summary-{days}d-{stamp}.json"
    mp = processed / f"bilibili-creator-summary-{days}d-{stamp}.md"
    jp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# {account} Bilibili creator data ({days} days)",
        "",
        f"Range: {start.isoformat()} → {latest.isoformat()}",
        f"Fan total: {result['fan_total']:,}",
        "",
        "## Daily fans",
        "",
        *[f"- {r['date']}: +{r['fan_inc']:,}" for r in fan_rows],
        "",
        "## Published videos",
        "",
    ]
    for v in videos:
        m = v["metrics"]
        detail = []
        if v.get("duration_s"):
            detail.append(f"{int(v['duration_s'])}s")
        if v.get("category"):
            detail.append(str(v["category"]))
        detail_text = f" ({', '.join(detail)})" if detail else ""
        lines.append(
            f"- {(v['published_at'] or '')[:16].replace('T', ' ')} `{v['content_id']}` {v['title']} — "
            f"{detail_text} "
            f"play {m.get('plays', 0):,}, fans {m.get('fans', 0):,}, coin {m.get('coins', 0):,}, "
            f"reply {m.get('comments', 0):,}, likes {m.get('likes', 0):,}, "
            f"fav {m.get('collects', 0):,}, share {m.get('shares', 0):,}, dm {m.get('danmaku', 0):,}"
        )
    mp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"ok": True, "json": str(jp), "markdown": str(mp),
            "fan_total": result["fan_total"], "videos": len(videos)}


def _days(n: int):
    from datetime import timedelta
    return timedelta(days=n)


# ── per-video detail (retention curve / completion / audience split) ──────
#
# The creator-center single-archive analysis ("稿件分析") exposes, per bvid/cid,
# what `summary` cannot: the retention/quit curve by playback second, average
# watch duration, completion vs same-tier peers, and the follower-vs-guest play
# split. These are READ via the same member.bilibili.com data APIs the creator
# center itself calls. All ratio fields are basis points (per 10000) — `_rate_pct`
# normalizes them to a 2-dp percent so consumers never have to guess the scale.

_ARCHIVE_VIEW = "https://member.bilibili.com/x/web/data/v3/archive/view"
_DIAG_OVERVIEW = "https://member.bilibili.com/x/web/data/archive_diagnose/overview"
_DIAG_PLAY = "https://member.bilibili.com/x/web/data/archive_diagnose/play_analyze"
_DIAG_TRANSFAN = "https://member.bilibili.com/x/web/data/archive_diagnose/trans_fans_analyze"
_ANALYZE_GRAPH = "https://member.bilibili.com/x/web/data/v2/archive/analyze/graph"


def _rate_pct(value: Any) -> float | None:
    """Bilibili creator-center ratios are basis points (per 10000) → percent, 2dp.

    e.g. raw 5223 → 52.23. ``None``/non-numeric → ``None`` so the key is dropped
    rather than emitted as a misleading 0.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v == 0:
        return 0.0
    return round(v / 100, 2)


def _share_pct(part: Any, other: Any) -> float | None:
    """``part`` as a percent of ``part + other``. Unit-agnostic — works whether the
    creator center hands back raw play counts or basis points (some archives return
    one, some the other for the fans/guest split), since it normalizes by the sum."""
    try:
        a, b = float(part), float(other)
    except (TypeError, ValueError):
        return None
    total = a + b
    return round(a / total * 100, 2) if total > 0 else None


def _retention_curve(quit_list: Any) -> list[dict[str, Any]]:
    """``[{duration_key, num}]`` → ``[{second, retained_pct}]``.

    ``num`` is basis-point retention (share still watching) at ``duration_key``
    seconds. Drops malformed points instead of guessing.
    """
    out: list[dict[str, Any]] = []
    for point in quit_list or []:
        if not isinstance(point, dict):
            continue
        sec, ret = point.get("duration_key"), _rate_pct(point.get("num"))
        if sec is None or ret is None:
            continue
        try:
            out.append({"second": int(sec), "retained_pct": ret})
        except (TypeError, ValueError):
            continue
    return out


# archive_diagnose/overview play_proportion keys → the labels Bilibili's 播放量来源
# pie uses. The new_* keys are the live ones; legacy keys are usually 0. This is a
# *terminal* split (the only "source" Bilibili exposes per video), not channel source.
_BILI_TERMINAL_LABELS = {
    "new_mobile": "移动", "new_pc": "PC", "new_ott": "TV", "new_h5": "H5", "new_others": "其他",
    "mobile": "移动", "pc": "PC", "ott": "TV", "tv": "TV", "h5": "H5",
    "android": "Android", "ios": "iOS", "other": "其他", "out": "站外",
}


def _terminal_distribution(play_proportion: Any) -> list[dict[str, Any]]:
    """``play_proportion`` raw counts → ``[{terminal, count, share_pct}]`` (UI labels,
    percent), aggregated by label and sorted desc. Drops zero buckets."""
    if not isinstance(play_proportion, dict):
        return []
    by_label: dict[str, int] = {}
    for key, val in play_proportion.items():
        if not isinstance(val, (int, float)) or val <= 0:
            continue
        label = _BILI_TERMINAL_LABELS.get(key, str(key))
        by_label[label] = by_label.get(label, 0) + int(val)
    total = sum(by_label.values())
    if not total:
        return []
    rows = [{"terminal": label, "count": cnt, "share_pct": round(cnt / total * 100, 2)}
            for label, cnt in by_label.items()]
    return sorted(rows, key=lambda r: -r["count"])


def _top_n(rows: Any, label_key: str, count_key: str, n: int) -> list[dict[str, Any]]:
    """Top-N of a ``[{label_key, count_key}]`` distribution, by count desc."""
    items = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        label, cnt = row.get(label_key), row.get(count_key)
        if label in (None, "") or cnt in (None, ""):
            continue
        try:
            items.append({"label": str(label), "count": int(cnt)})
        except (TypeError, ValueError):
            continue
    return sorted(items, key=lambda r: -r["count"])[:n]


def _bili_detail_row(*, account: str, captured: str, view: dict[str, Any],
                     overview: dict[str, Any], play_analyze: dict[str, Any],
                     graph: dict[str, Any], trans: dict[str, Any]) -> dict[str, Any]:
    """Build one canonical per-video row enriched with a ``detail`` block.

    Pure given the four already-fetched API payloads, so it is unit-tested offline.
    """
    stat = (overview or {}).get("stat") or {}
    quit_info = (graph or {}).get("quit_info") or {}
    aud = (overview or {}).get("audience_proportion") or {}
    assistant = (play_analyze or {}).get("viewer_assistant") or {}
    arc = (play_analyze or {}).get("arc_audience") or {}
    bvid = str(view.get("bvid") or "")
    duration_s = _duration_seconds(view.get("duration"))
    pub = _date_from_epoch(view.get("pubtime"))

    # Follower vs non-follower play split. audience_proportion.fans/guest are raw play
    # COUNTS (not basis points), so normalize by their sum; fall back to play_analyze's
    # viewer_assistant rates (those ARE basis points) when the split is absent.
    follower_pct = _share_pct(aud.get("fans"), aud.get("guest"))
    guest_pct = _share_pct(aud.get("guest"), aud.get("fans"))
    if follower_pct is None:
        follower_pct = _rate_pct(assistant.get("play_fan_rate"))
        guest_pct = _rate_pct(assistant.get("play_viewer_rate"))

    # avg_play_progress is the average watched seconds; full_play_ratio is the
    # average watched FRACTION (avg_play_progress / duration), basis points — note
    # that is "average completion", not "% who reached the end".
    metrics = {
        "plays": _stat_int(stat, view, names=("play", "view")),
        "likes": _stat_int(stat, names=("like",)),
        "comments": _stat_int(stat, names=("comment",)),
        "coins": _stat_int(stat, names=("coin",)),
        "collects": _stat_int(stat, names=("fav", "favorite")),
        "shares": _stat_int(stat, names=("share",)),
        "danmaku": _stat_int(stat, names=("dm",)),
        "fans": _stat_int(stat, view, names=("fan", "fans", "fans_incr")),
        "avg_watch_duration_s": quit_info.get("avg_play_progress"),
        "avg_completion_pct": _rate_pct(quit_info.get("full_play_ratio")),
        "follower_play_ratio_pct": follower_pct,
        "guest_play_ratio_pct": guest_pct,
    }
    row = schema.video_row(
        platform="bilibili", account=account, content_id=bvid or None,
        title=view.get("title"), published_at=pub.isoformat() if pub else None,
        captured_at=captured,
        source_url=f"https://www.bilibili.com/video/{bvid}" if bvid else None,
        metrics={k: v for k, v in metrics.items() if v is not None})
    if duration_s:
        row["duration_s"] = duration_s
    if view.get("cover"):
        row["cover_url"] = view["cover"]
    if str(view.get("copyright")) in ("1", "2"):
        row["copyright"] = view["copyright"]
        row["is_original"] = str(view["copyright"]) == "1"

    detail: dict[str, Any] = {
        "retention_curve": _retention_curve((graph or {}).get("viewer_quit")),
        "peer_retention_curve": _retention_curve((graph or {}).get("peer_viewer_quit")),
        "completion": {
            "avg_watch_duration_s": quit_info.get("avg_play_progress"),
            "avg_completion_pct": _rate_pct(quit_info.get("full_play_ratio")),
            "peer_avg_completion_pct": _rate_pct(quit_info.get("full_play_ratio_avg")),
            "peer_median_completion_pct": _rate_pct(quit_info.get("full_play_ratio_medium")),
            "vs_peers_percentile_pct": _rate_pct(quit_info.get("pass_peer")),
        },
        "audience_play_split": {
            "follower_pct": follower_pct,
            "non_follower_pct": guest_pct,
        },
        "terminal_distribution": _terminal_distribution((overview or {}).get("play_proportion")),
        "demographics": {
            "gender": (overview or {}).get("gender"),
            "age": (overview or {}).get("viewer_age"),
            "top_regions": _top_n((overview or {}).get("viewer_area"), "location", "count", 8),
            "top_interest_tags": _top_n((overview or {}).get("viewer_ty"), "tag_name", "count", 10),
        },
        "vs_peers": {
            "play_pass_rate_percentile_pct": _rate_pct(arc.get("play_viewer_pass_rate")),
        },
    }
    ptf = (trans or {}).get("play_trans_fan") or {}
    if ptf:
        detail["fan_conversion"] = {
            "new_fans": ptf.get("total_new_attention_cnt"),
            "viewer_play_cnt": ptf.get("viewer_play_cnt"),
            "vs_peers_percentile_pct": _rate_pct(ptf.get("total_play_trans_fan_pass_per")),
        }

    def _clean(obj: Any) -> Any:
        if isinstance(obj, dict):
            cleaned = {k: _clean(v) for k, v in obj.items()}
            return {k: v for k, v in cleaned.items() if v not in (None, {}, [])}
        return obj

    row["detail"] = _clean(detail)
    return row


def _detail_field_notes() -> dict[str, str]:
    return {
        "metrics.avg_watch_duration_s": "Average watched seconds (creator-center avg_play_progress).",
        "metrics.avg_completion_pct": "Average watched fraction = avg_play_progress / duration, percent. "
                                      "This is mean completion, NOT the share of viewers who reached the end.",
        "metrics.follower_play_ratio_pct / guest_play_ratio_pct": "Share of plays from followers vs non-followers, percent.",
        "detail.retention_curve": "[{second, retained_pct}] — share still watching at each playback second.",
        "detail.completion.vs_peers_percentile_pct": "Percentile vs same-tier peers (higher = better than more peers).",
        "detail.terminal_distribution": "Plays by client/terminal (Bilibili '播放量来源' is terminal split, "
                                        "not a recommend/search traffic-source breakdown — that is not exposed per-video).",
    }


def video_detail(*, ws: Path, account: str, credential_path: Path, bvid: str,
                 with_peers: bool = True) -> dict[str, Any]:
    creds = load_credentials(credential_path)
    cookie = cookie_header(creds)
    referer = "https://member.bilibili.com/york/data-center-web"
    with _client(cookie, referer) as c:
        nav = _get_json(c, "https://api.bilibili.com/x/web-interface/nav").get("data") or {}
        if not nav.get("isLogin"):
            raise CollectorError("Bilibili cookie invalid or expired; re-export SESSDATA/bili_jct.")
        view = _get_json(c, _ARCHIVE_VIEW, {"bvid": bvid, "t": int(time.time() * 1000)}).get("data") or {}
        if not view.get("bvid"):
            raise CollectorError(f"Bilibili archive view returned no data for {bvid} — wrong bvid or not your archive")
        videos = view.get("videos") or []
        cid = (videos[0].get("cid") if videos and isinstance(videos[0], dict) else None) or view.get("cid")
        overview = _get_json(c, _DIAG_OVERVIEW, {"bvid": bvid, "t": int(time.time() * 1000)}).get("data") or {}
        play_analyze = _get_json(c, _DIAG_PLAY, {"bvid": bvid, "t": int(time.time() * 1000)}).get("data") or {}
        graph: dict[str, Any] = {}
        if cid:
            graph = _get_json(c, _ANALYZE_GRAPH, {"cid": cid, "t": int(time.time() * 1000)}).get("data") or {}
        trans: dict[str, Any] = {}
        if with_peers:
            try:
                trans = _get_json(c, _DIAG_TRANSFAN, {"bvid": bvid, "t": int(time.time() * 1000)}).get("data") or {}
            except CollectorError:
                trans = {}

    captured = datetime.now(TZ).isoformat()
    row = _bili_detail_row(account=account, captured=captured, view=view, overview=overview,
                           play_analyze=play_analyze, graph=graph, trans=trans)
    result = {
        "schema_version": schema.SCHEMA_VERSION,
        "account": account,
        "platform": "bilibili",
        "source": "Bilibili creator-center single-archive analysis (稿件分析) APIs",
        "captured_at": captured,
        "bvid": row["content_id"],
        "cid": cid,
        "field_notes": _detail_field_notes(),
        "video": row,
    }
    raw, processed = output_dirs(ws, account, "bilibili")
    stamp = _stamp()
    jp = raw / f"bilibili-video-detail-{row['content_id']}-{stamp}.json"
    mp = processed / f"bilibili-video-detail-{row['content_id']}-{stamp}.md"
    jp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    mp.write_text(_render_detail_md(row), encoding="utf-8")
    m = row["metrics"]
    return {"ok": True, "json": str(jp), "markdown": str(mp), "bvid": row["content_id"],
            "avg_watch_duration_s": m.get("avg_watch_duration_s"),
            "avg_completion_pct": m.get("avg_completion_pct"),
            "retention_points": len(row.get("detail", {}).get("retention_curve") or [])}


def _render_detail_md(row: dict[str, Any]) -> str:
    m = row["metrics"]
    d = row.get("detail") or {}
    comp = d.get("completion") or {}
    lines = [
        f"# 稿件分析：{row.get('title') or row.get('content_id')}",
        "",
        f"- bvid: `{row.get('content_id')}`",
        f"- 时长：{int(row['duration_s'])} 秒" if row.get("duration_s") else "",
        f"- 播放：{m.get('plays', 0):,}",
        f"- 平均观看时长：{m.get('avg_watch_duration_s', '—')} 秒",
        f"- 平均完播（avg_play_progress/时长）：{m.get('avg_completion_pct', '—')}%"
        + (f"（同类均值 {comp.get('peer_avg_completion_pct')}%）" if comp.get("peer_avg_completion_pct") is not None else ""),
        f"- 关注/非关注播放占比：{m.get('follower_play_ratio_pct', '—')}% / {m.get('guest_play_ratio_pct', '—')}%",
        "",
        "## 留存曲线（每秒仍在观看占比）",
        "",
        "| 秒 | 留存% | 同类% |",
        "|---:|---:|---:|",
    ]
    peer = {p["second"]: p["retained_pct"] for p in (d.get("peer_retention_curve") or [])}
    for p in (d.get("retention_curve") or []):
        lines.append(f"| {p['second']} | {p['retained_pct']} | {peer.get(p['second'], '')} |")
    terminals = d.get("terminal_distribution") or []
    if terminals:
        lines += ["", "## 播放量来源（终端）", "",
                  "、".join(f"{t['terminal']} {t['share_pct']}%" for t in terminals)]
    regions = d.get("demographics", {}).get("top_regions") or []
    if regions:
        lines += ["", "## 观众地域 Top",
                  "", "、".join(f"{r['label']}({r['count']:,})" for r in regions[:8])]
    return "\n".join(x for x in lines if x != "") + "\n"


# ── fan sources ──────────────────────────────────────────────────────────

_FAN_SOURCE_LABELS = {
    "video": "video",
    "article": "article",
    "live": "live",
    "space": "space",
    "search": "search",
    "recommend": "recommend",
    "other": "other",
}


def _fan_source_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    total = sum(int(v or 0) for v in data.values() if isinstance(v, (int, float)))
    rows = []
    for key, value in sorted(data.items(), key=lambda item: -(int(item[1] or 0) if isinstance(item[1], (int, float)) else 0)):
        if not isinstance(value, (int, float)):
            continue
        count = int(value)
        rows.append({
            "source_key": key,
            "source_label": _FAN_SOURCE_LABELS.get(key, key),
            "count": count,
            "share_pct": round(count / total * 100, 2) if total else 0.0,
        })
    return rows


def fan_source(*, ws: Path, account: str, credential_path: Path) -> dict[str, Any]:
    creds = load_credentials(credential_path)
    referer = "https://member.bilibili.com/platform/data-up/fans-analysis"
    with _client(cookie_header(creds), referer) as c:
        data = _get_json(c, "https://member.bilibili.com/x/web/data/v2/fans/stat/source").get("data") or {}
    if not isinstance(data, dict) or not data:
        raise CollectorError("no Bilibili fan source data returned (cookie may lack creator access)")

    rows = _fan_source_rows(data)
    captured = datetime.now(TZ).isoformat()
    raw, processed = output_dirs(ws, account, "bilibili")
    stamp = _stamp()
    result = {
        "schema_version": schema.SCHEMA_VERSION,
        "account": account,
        "platform": "bilibili",
        "source": "Bilibili creator-center /x/web/data/v2/fans/stat/source",
        "captured_at": captured,
        "source_total": sum(r["count"] for r in rows),
        "sources": rows,
    }
    jp = raw / f"bilibili-fan-source-{stamp}.json"
    mp = processed / f"bilibili-fan-source-{stamp}.md"
    jp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"# {account} Bilibili fan source", "",
             f"Captured at: {captured}",
             f"Total: {result['source_total']:,}", "",
             "| Source | Count | Share |", "|---|---:|---:|"]
    for r in rows:
        lines.append(f"| {r['source_label']} | {r['count']:,} | {r['share_pct']:.2f}% |")
    mp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"ok": True, "json": str(jp), "markdown": str(mp),
            "source_total": result["source_total"], "sources": len(rows)}


# ── comments ─────────────────────────────────────────────────────────────

def _resolve_video(client: httpx.Client, *, bvid: str | None, aid: int | None) -> tuple[str, int]:
    """Resolve (bvid, aid) via the view API.

    The offline BV1↔aid math only covers pre-2023 (~32-bit) aids and silently
    breaks for long aids. Always go through the API.
    """
    params = {"bvid": bvid} if bvid else {"aid": aid}
    d = _get_json(client, "https://api.bilibili.com/x/web-interface/view", params).get("data") or {}
    if not d.get("bvid"):
        raise CollectorError(f"could not resolve video from {params}")
    return d["bvid"], int(d["aid"])


def fetch_comments(client: httpx.Client, *, aid: int, max_pages: int, delay_ms: int) -> list[dict[str, Any]]:
    """Top-level comments via x/v2/reply/main (mode=3, cursor pagination).

    Never use x/v2/reply/wbi/main — it silently returns only ~3 hot comments.
    The ``pn=N`` param looks like it paginates but returns page 1 every time on
    mode=3; the opaque ``cursor.pagination_reply.next_offset`` token is the real
    cursor. Deduped by rpid.
    """
    by_rpid: dict[int, dict[str, Any]] = {}
    base = f"https://api.bilibili.com/x/v2/reply/main?type=1&oid={aid}&mode=3&ps=20"
    next_offset: str | None = None
    for page in range(1, max_pages + 1):
        url = base
        if next_offset:
            pag = urllib.parse.quote(json.dumps({"offset": next_offset}, separators=(",", ":")))
            url = f"{base}&pagination_str={pag}"
        body = _get_json(client, url).get("data") or {}
        replies = body.get("replies") or []
        if not replies:
            break
        new = 0
        for r in replies:
            rpid = r.get("rpid")
            if rpid is None or rpid in by_rpid:
                continue
            by_rpid[rpid] = {
                "rpid": rpid,
                "mid": r.get("mid"),
                "uname": (r.get("member") or {}).get("uname"),
                "message": (r.get("content") or {}).get("message"),
                "ctime": r.get("ctime"),
                "like": r.get("like"),
                "rcount": r.get("rcount"),
            }
            new += 1
        cursor = body.get("cursor") or {}
        if cursor.get("is_end") or new == 0:
            break
        if len(by_rpid) >= cursor.get("all_count", 0):
            break
        next_offset = (cursor.get("pagination_reply") or {}).get("next_offset")
        if not next_offset:
            break
        if page < max_pages:
            time.sleep(delay_ms / 1000)
    return list(by_rpid.values())


def comments(
    *, ws: Path, account: str, bvid: str | None, aid: int | None,
    sessdata: str | None, max_pages: int, delay_ms: int,
) -> dict[str, Any]:
    if not sessdata:
        raise CollectorError(
            "B站 comments require a login cookie (SESSDATA). Pass --sessdata or set up "
            "the account credential file; anonymous calls trigger 412."
        )
    with _client(f"SESSDATA={sessdata}") as c:
        bvid, aid = _resolve_video(c, bvid=bvid, aid=aid)
        rows = fetch_comments(c, aid=aid, max_pages=max_pages, delay_ms=delay_ms)
    raw, processed = output_dirs(ws, account, "bilibili")
    stamp = _stamp()
    jp = raw / f"bilibili-comments-{bvid}-{stamp}.json"
    result = {
        "account": account, "platform": "bilibili", "bvid": bvid, "aid": aid,
        "collected_at": datetime.now(TZ).isoformat(),
        "comment_count": len(rows),
        "comments": sorted(rows, key=lambda r: -(r.get("like") or 0)),
    }
    jp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "json": str(jp), "bvid": bvid, "aid": aid, "comments": len(rows)}


# ── danmaku ──────────────────────────────────────────────────────────────

def _video_info(client: httpx.Client, bvid: str) -> dict[str, Any]:
    d = _get_json(client, "https://api.bilibili.com/x/web-interface/view", {"bvid": bvid}).get("data") or {}
    return {
        "aid": d.get("aid"), "cid": d.get("cid"), "title": d.get("title"),
        "duration_s": d.get("duration"),
        "pages": [
            {"page": p.get("page"), "cid": p.get("cid"), "part": p.get("part"),
             "duration": p.get("duration")}
            for p in (d.get("pages") or [])
        ],
    }


def _read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while pos < len(buf):
        b = buf[pos]
        pos += 1
        value |= (b & 0x7F) << shift
        if not b & 0x80:
            return value, pos
        shift += 7
        if shift > 63:
            break
    raise CollectorError("invalid Bilibili danmaku protobuf: unterminated varint")


def _iter_proto_fields(buf: bytes):
    pos = 0
    while pos < len(buf):
        key, pos = _read_varint(buf, pos)
        field, wire_type = key >> 3, key & 0x07
        if wire_type == 0:
            value, pos = _read_varint(buf, pos)
            yield field, wire_type, value
        elif wire_type == 1:
            end = pos + 8
            if end > len(buf):
                raise CollectorError("invalid Bilibili danmaku protobuf: truncated fixed64")
            yield field, wire_type, buf[pos:end]
            pos = end
        elif wire_type == 2:
            size, pos = _read_varint(buf, pos)
            end = pos + size
            if end > len(buf):
                raise CollectorError("invalid Bilibili danmaku protobuf: truncated bytes")
            yield field, wire_type, buf[pos:end]
            pos = end
        elif wire_type == 5:
            end = pos + 4
            if end > len(buf):
                raise CollectorError("invalid Bilibili danmaku protobuf: truncated fixed32")
            yield field, wire_type, buf[pos:end]
            pos = end
        else:
            raise CollectorError(f"unsupported Bilibili danmaku protobuf wire type {wire_type}")


def _decode_danmaku_elem(buf: bytes) -> dict[str, Any]:
    row: dict[str, Any] = {"time_s": 0.0, "type": 0, "pool": 0, "content": ""}
    for field, wire_type, value in _iter_proto_fields(buf):
        if field == 2 and wire_type == 0:      # progress, ms
            row["time_s"] = int(value) / 1000
        elif field == 3 and wire_type == 0:    # mode
            row["type"] = int(value)
        elif field == 7 and wire_type == 2:    # content
            row["content"] = bytes(value).decode("utf-8", errors="replace")
        elif field == 11 and wire_type == 0:   # pool
            row["pool"] = int(value)
    return row


def _decode_danmaku_seg(buf: bytes) -> list[dict[str, Any]]:
    rows = []
    for field, wire_type, value in _iter_proto_fields(buf):
        if field == 1 and wire_type == 2:
            rows.append(_decode_danmaku_elem(bytes(value)))
    return rows


def fetch_danmaku(client: httpx.Client, cid: int, *, max_segments: int = 1000) -> list[dict[str, Any]]:
    """Danmaku via Bilibili's protobuf segment endpoint.

    The legacy XML endpoint (x/v1/dm/list.so) now returns non-raw-deflate payloads
    for some current videos. seg.so returns roughly six-minute protobuf segments;
    loop until the first empty segment, keeping analyze_danmaku's row shape.
    """
    dms: list[dict[str, Any]] = []
    for segment_index in range(1, max_segments + 1):
        resp = client.get(
            "https://api.bilibili.com/x/v2/dm/web/seg.so",
            params={"type": 1, "oid": cid, "segment_index": segment_index},
        )
        if getattr(resp, "status_code", 200) == 304:
            break
        resp.raise_for_status()
        rows = _decode_danmaku_seg(resp.content)
        if not rows:
            break
        dms.extend(rows)
    else:
        raise CollectorError(
            f"Bilibili danmaku still had data after {max_segments} segments; "
            "refusing an unbounded fetch"
        )
    return dms


def _keywords(texts: list[str], top_n: int = 8) -> list[tuple[str, int]]:
    words: Counter = Counter()
    for t in texts:
        for w in re.findall(r"[一-鿿]{2,4}", t):
            words[w] += 1
        for w in re.findall(r"[A-Za-z0-9]{3,}", t):
            words[w.upper()] += 1
    return words.most_common(top_n)


def analyze_danmaku(
    danmaku: list[dict[str, Any]], *, title: str = "",
    bucket_s: int = 10, peak_n: int = 5, peak_method: str = "topn", filter_pool1: bool = True,
    video_duration_s: float | None = None,
) -> dict[str, Any]:
    dms = [d for d in danmaku if d.get("pool", 0) != 1] if filter_pool1 else list(danmaku)
    if not dms:
        return {"title": title, "error": "no danmaku after filtering", "total_danmaku": 0, "peaks": []}
    # Prefer the real video length; danmaku can stop well before the end, which would
    # understate duration and inflate density_per_min. Fall back to the last danmaku.
    last_dm = max(d.get("time_s", 0) for d in dms)
    duration = float(video_duration_s) if video_duration_s else last_dm
    buckets: Counter = Counter()
    bucket_content: dict[int, list[str]] = defaultdict(list)
    for d in dms:
        key = int(d["time_s"] // bucket_s) * bucket_s
        buckets[key] += 1
        bucket_content[key].append(d["content"])
    if peak_method == "zscore" and len(buckets) >= 5:
        counts = list(buckets.values())
        mean_c, stdev_c = statistics.mean(counts), statistics.stdev(counts)
        peaks = sorted(
            ((ts, cnt) for ts, cnt in buckets.items() if (cnt - mean_c) / max(stdev_c, 0.01) > 1.5),
            key=lambda x: -x[1],
        )[:peak_n]
    else:
        peaks = buckets.most_common(peak_n)
    peak_details = []
    for ts, count in peaks:
        contents = bucket_content[ts]
        mm, ss = ts // 60, ts % 60
        peak_details.append({
            "start_s": ts, "end_s": ts + bucket_s,
            "time_label": f"{mm:02d}:{ss:02d}-{(ts + bucket_s) // 60:02d}:{(ts + bucket_s) % 60:02d}",
            "count": count,
            "keywords": _keywords(contents),
            "sample_quotes": sorted(set(contents), key=lambda x: -len(x))[:5],
        })
    return {
        "title": title,
        "total_danmaku": len(dms),
        "duration_s": duration,
        "density_per_min": round(len(dms) / max(duration / 60, 0.1), 1),
        "bucket_size_s": bucket_s,
        "peaks": peak_details,
        "top_keywords": _keywords([d["content"] for d in dms], top_n=20),
    }


def render_danmaku_md(analysis: dict[str, Any]) -> str:
    if analysis.get("error"):
        return f"# 弹幕分析：{analysis.get('title', '(未知)')}\n\n❌ {analysis['error']}\n"
    lines = [
        f"# 弹幕分析：{analysis.get('title', '(未知)')}",
        "",
        "## 概览",
        f"- 总弹幕数：{analysis['total_danmaku']} 条",
        f"- 视频时长：{int(analysis['duration_s'])} 秒",
        f"- 弹幕密度：{analysis['density_per_min']} 条/分钟",
        "",
        "## 弹幕密度峰值",
        "",
    ]
    for i, peak in enumerate(analysis.get("peaks", []), 1):
        lines.append(f"### 峰值 {i}：{peak['time_label']}（{peak['count']} 条）")
        if peak.get("keywords"):
            lines.append(f"**关键话题**：{'、'.join(kw for kw, _ in peak['keywords'][:5])}")
        lines.append("**代表性弹幕：**")
        lines += [f"- 「{q}」" for q in peak.get("sample_quotes", [])[:3]]
        lines.append("")
    if analysis.get("top_keywords"):
        lines += ["## 高频关键词", "", "、".join(f"{kw}({cnt})" for kw, cnt in analysis["top_keywords"][:15]), ""]
    return "\n".join(lines) + "\n"


def danmaku(
    *, ws: Path, account: str, bvid: str | None, cid: int | None,
    bucket_s: int, peak_n: int, peak_method: str, filter_pool1: bool,
) -> dict[str, Any]:
    raw, processed = output_dirs(ws, account, "bilibili")
    stamp = _stamp()
    with _client() as c:
        if cid:
            targets = [(cid, "", None)]
            title = f"cid={cid}"
        else:
            info = _video_info(c, bvid)
            title = info["title"] or bvid
            pages = info["pages"] or [{"cid": info["cid"], "part": title, "page": 1,
                                       "duration": info.get("duration_s")}]
            targets = [(p["cid"], p.get("part", ""), p.get("duration")) for p in pages]
        out = []
        for target_cid, part, dur in targets:
            dms = fetch_danmaku(c, target_cid)
            part_title = part or title
            analysis = analyze_danmaku(
                dms, title=part_title, bucket_s=bucket_s, peak_n=peak_n,
                peak_method=peak_method, filter_pool1=filter_pool1,
                video_duration_s=dur,
            )
            jp = raw / f"bilibili-danmaku-{target_cid}-{stamp}.json"
            mp = processed / f"bilibili-danmaku-{target_cid}-{stamp}.md"
            jp.write_text(json.dumps(
                {"cid": target_cid, "title": part_title, "count": len(dms),
                 "fetched_at": datetime.now(TZ).isoformat(),
                 "danmaku": dms, "analysis": analysis},
                ensure_ascii=False, indent=2), encoding="utf-8")
            mp.write_text(render_danmaku_md(analysis), encoding="utf-8")
            out.append({"cid": target_cid, "count": len(dms), "json": str(jp), "markdown": str(mp)})
    return {"ok": True, "parts": out}
