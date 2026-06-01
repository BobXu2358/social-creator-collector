"""Bilibili: plain-HTTPS creator-center and public-data collection.

Why no browser here: the three creator cookies (``SESSDATA``, ``bili_jct``,
``buvid3``) sent as a ``Cookie`` header are enough for the creator-center and
comment APIs, so Bilibili stays a lightweight httpx path. Douyin can't — see
``douyin.py``.

Commands: probe, summary, comments, danmaku.
"""
from __future__ import annotations

import asyncio
import json
import re
import statistics
import sys
import time
import urllib.parse
import zlib
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from . import schema
from .paths import TZ, CollectorError, output_dirs

REQUIRED_FIELDS = ("SESSDATA", "bili_jct", "buvid3")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


# ── credentials ──────────────────────────────────────────────────────────

def load_credentials(path: Path, *, required: tuple[str, ...] = REQUIRED_FIELDS) -> dict[str, str]:
    if not path.exists():
        raise CollectorError(f"missing Bilibili credential file: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    missing = [k for k in required if not data.get(k)]
    if missing:
        raise CollectorError(f"Bilibili credential missing fields {missing}; path={path}")
    return {k: str(data[k]) for k in required if data.get(k)}


def cookie_header(creds: dict[str, str]) -> str:
    return "; ".join(f"{k}={v}" for k, v in creds.items())


def _client(cookie: str | None = None, referer: str = "https://www.bilibili.com/") -> httpx.Client:
    headers = {"User-Agent": _UA, "Referer": referer, "Accept": "application/json, text/plain, */*"}
    if cookie:
        headers["Cookie"] = cookie
    return httpx.Client(headers=headers, timeout=30, follow_redirects=True)


def _get_json(client: httpx.Client, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    resp = client.get(url, params=params or {})
    resp.raise_for_status()
    obj = resp.json()
    code = obj.get("code")
    if code not in (0, None):
        # -412/-799/-352/-403 are risk-control codes; do not hammer on these.
        raise CollectorError(
            f"Bilibili API error code={code} message={obj.get('message')!r} url={resp.url}"
        )
    return obj


def _stamp() -> str:
    return datetime.now(TZ).strftime("%Y%m%d-%H%M%S")


# ── probe ────────────────────────────────────────────────────────────────

def probe(*, ws: Path, account: str, credential_path: Path) -> dict[str, Any]:
    creds = load_credentials(credential_path)
    with _client(cookie_header(creds)) as c:
        data = _get_json(c, "https://api.bilibili.com/x/web-interface/nav").get("data") or {}
    if not data.get("isLogin"):
        raise CollectorError(
            "Bilibili cookie invalid or expired — re-export SESSDATA/bili_jct/buvid3."
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

    out = {k: creds.get(k, "") for k in REQUIRED_FIELDS}
    missing = [k for k in REQUIRED_FIELDS if not out[k]]
    if missing:
        raise CollectorError(f"login succeeded but missing cookies {missing} — try again")
    credential_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        credential_path.chmod(0o600)
    except OSError:
        pass
    return {"ok": True, "credential": str(credential_path), "mid": mid, "method": "qr-login"}


# ── creator-center summary (fan trend + per-video stats) ─────────────────

def summary(*, ws: Path, account: str, credential_path: Path, days: int) -> dict[str, Any]:
    creds = load_credentials(credential_path)
    cookie = cookie_header(creds)
    referer = "https://member.bilibili.com/york/data-center-web?tmid=&bvid=&tab="
    with _client(cookie, referer) as c:
        # fail loud before doing real work
        nav = _get_json(c, "https://api.bilibili.com/x/web-interface/nav").get("data") or {}
        if not nav.get("isLogin"):
            raise CollectorError(
                "Bilibili cookie invalid or expired — re-export SESSDATA/bili_jct/buvid3."
            )

        fan_obj = _get_json(
            c,
            "https://member.bilibili.com/x/web/data/v2/overview/stat/graph",
            {"period": 1, "s_locale": "zh_CN", "type": "fan", "tmid": "", "t": int(time.time() * 1000)},
        )
        trend = (fan_obj.get("data") or {}).get("tendency") or []
        if not trend:
            raise CollectorError("no Bilibili fan trend returned (cookie may lack creator access)")
        latest = max(datetime.fromtimestamp(x["date_key"], TZ).date() for x in trend)
        start = latest - _days(days - 1)
        captured = datetime.now(TZ).isoformat()
        fan_rows = sorted(
            (
                schema.fan_trend_row(
                    platform="bilibili", account=account,
                    date=datetime.fromtimestamp(x["date_key"], TZ).date().isoformat(),
                    fan_inc=int(x.get("total_inc") or 0), captured_at=captured,
                )
                for x in trend
                if start <= datetime.fromtimestamp(x["date_key"], TZ).date() <= latest
            ),
            key=lambda r: r["date"],
        )

        videos: list[dict[str, Any]] = []
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
                pub = datetime.fromtimestamp(int(it["pubtime"]), TZ)
                if start <= pub.date() <= latest:
                    stat = it.get("real_stat") or it.get("stat") or {}
                    videos.append(schema.video_row(
                        platform="bilibili", account=account, content_id=it.get("bvid"),
                        title=it.get("title"), published_at=pub.isoformat(), captured_at=captured,
                        source_url=f"https://www.bilibili.com/video/{it.get('bvid')}",
                        metrics={
                            "plays": int(stat.get("play") or 0),
                            "likes": int(stat.get("likes") or 0),
                            "comments": int(stat.get("reply") or 0),
                            "coins": int(stat.get("coin") or 0),
                            "fans": int(stat.get("fans") or 0),
                            "full_play_ratio": stat.get("full_play_ratio"),
                        }))
            if datetime.fromtimestamp(int(items[-1]["pubtime"]), TZ).date() < start:
                break
    videos.sort(key=lambda r: r["published_at"] or "", reverse=True)

    result = {
        "schema_version": schema.SCHEMA_VERSION,
        "account": account,
        "platform": "bilibili",
        "source": "Bilibili creator-center APIs",
        "range": {"start": start.isoformat(), "end": latest.isoformat(), "days": days},
        "captured_at": captured,
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
        lines.append(
            f"- {(v['published_at'] or '')[:16].replace('T', ' ')} `{v['content_id']}` {v['title']} — "
            f"play {m.get('plays', 0):,}, fans {m.get('fans', 0):,}, coin {m.get('coins', 0):,}, "
            f"reply {m.get('comments', 0):,}, likes {m.get('likes', 0):,}"
        )
    mp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"ok": True, "json": str(jp), "markdown": str(mp),
            "fan_total": result["fan_total"], "videos": len(videos)}


def _days(n: int):
    from datetime import timedelta
    return timedelta(days=n)


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
        "collected_at": datetime.now(timezone.utc).isoformat(),
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
            {"page": p.get("page"), "cid": p.get("cid"), "part": p.get("part")}
            for p in (d.get("pages") or [])
        ],
    }


def fetch_danmaku(client: httpx.Client, cid: int) -> list[dict[str, Any]]:
    """Danmaku via the deflate-compressed XML endpoint (x/v1/dm/list.so).

    Not gzip, not raw XML — decompress with ``zlib.decompress(raw, -MAX_WBITS)``.
    Works without auth for public videos; >5000 danmaku may be truncated (the
    protobuf seg endpoint would be needed then).
    """
    resp = client.get("https://api.bilibili.com/x/v1/dm/list.so", params={"oid": cid})
    resp.raise_for_status()
    text = zlib.decompress(resp.content, -zlib.MAX_WBITS).decode("utf-8", errors="replace")
    dms: list[dict[str, Any]] = []
    for m in re.finditer(r'<d p="([^"]*)">(.*?)</d>', text):
        a = m.group(1).split(",")
        dms.append({
            "time_s": float(a[0]) if a and a[0] else 0.0,
            "type": int(a[1]) if len(a) > 1 else 0,
            "color": int(a[3]) if len(a) > 3 else 0,
            "ctime": int(a[4]) if len(a) > 4 else 0,
            "pool": int(a[5]) if len(a) > 5 else 0,
            "content": m.group(2),
        })
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
) -> dict[str, Any]:
    dms = [d for d in danmaku if d.get("pool", 0) != 1] if filter_pool1 else list(danmaku)
    if not dms:
        return {"title": title, "error": "no danmaku after filtering", "total_danmaku": 0, "peaks": []}
    duration = max(d.get("time_s", 0) for d in dms)
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
            targets = [(cid, "")]
            title = f"cid={cid}"
        else:
            info = _video_info(c, bvid)
            title = info["title"] or bvid
            pages = info["pages"] or [{"cid": info["cid"], "part": title, "page": 1}]
            targets = [(p["cid"], p.get("part", "")) for p in pages]
        out = []
        for target_cid, part in targets:
            dms = fetch_danmaku(c, target_cid)
            part_title = part or title
            analysis = analyze_danmaku(
                dms, title=part_title, bucket_s=bucket_s, peak_n=peak_n,
                peak_method=peak_method, filter_pool1=filter_pool1,
            )
            jp = raw / f"bilibili-danmaku-{target_cid}-{stamp}.json"
            mp = processed / f"bilibili-danmaku-{target_cid}-{stamp}.md"
            jp.write_text(json.dumps(
                {"cid": target_cid, "title": part_title, "count": len(dms),
                 "fetched_at": datetime.now(timezone.utc).isoformat(),
                 "danmaku": dms, "analysis": analysis},
                ensure_ascii=False, indent=2), encoding="utf-8")
            mp.write_text(render_danmaku_md(analysis), encoding="utf-8")
            out.append({"cid": target_cid, "count": len(dms), "json": str(jp), "markdown": str(mp)})
    return {"ok": True, "parts": out}
