#!/usr/bin/env python3
"""Read-only Bilibili/Douyin creator data collector.

Designed for agents. It never prints cookie values. Keep all credential files under
`social/_secrets/<account>/<platform>/` and outputs under `social/<account>/<platform>/`.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")


def die(msg: str, code: int = 2) -> None:
    raise SystemExit(f"ERROR: {msg}")


def account_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", value or ""):
        die("account/profile names may contain only letters, digits, _ . -")
    return value


def workspace_root(args: argparse.Namespace) -> Path:
    return Path(args.workspace).expanduser().resolve()


def secret_dir(ws: Path, account: str, platform: str) -> Path:
    return ws / "social" / "_secrets" / account / platform


def output_dirs(ws: Path, account: str, platform: str) -> tuple[Path, Path]:
    raw = ws / "social" / account / platform / "raw"
    processed = ws / "social" / account / platform / "processed"
    raw.mkdir(parents=True, exist_ok=True)
    processed.mkdir(parents=True, exist_ok=True)
    return raw, processed


def init_account(args: argparse.Namespace) -> int:
    ws = workspace_root(args); acct = account_name(args.account)
    created = []
    for platform in ["bilibili", "douyin"]:
        sd = secret_dir(ws, acct, platform); raw, processed = output_dirs(ws, acct, platform)
        sd.mkdir(parents=True, exist_ok=True)
        (ws / "social" / acct / platform / "scripts").mkdir(parents=True, exist_ok=True)
        created.extend([str(sd), str(raw), str(processed)])
    bili = secret_dir(ws, acct, "bilibili") / "default.credentials.example.json"
    if not bili.exists():
        bili.write_text(json.dumps({"SESSDATA":"paste_here","bili_jct":"paste_here","buvid3":"paste_here"}, ensure_ascii=False, indent=2), encoding="utf-8")
        created.append(str(bili))
    douyin = secret_dir(ws, acct, "douyin") / "default.cookies.example.json"
    if not douyin.exists():
        douyin.write_text("[]\n", encoding="utf-8"); created.append(str(douyin))
    print(json.dumps({"ok": True, "account": acct, "created_or_present": created}, ensure_ascii=False, indent=2))
    return 0


# ---------------- Bilibili ----------------

def load_bilibili_credentials(path: Path) -> dict[str, str]:
    if not path.exists(): die(f"missing Bilibili credential file: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    required = ["SESSDATA", "bili_jct", "buvid3"]
    missing = [k for k in required if not data.get(k)]
    if missing: die(f"Bilibili credential missing fields: {missing}; path={path}")
    return {k: str(data[k]) for k in required}


def request_json(url: str, params: dict[str, object], cookie: str, referer: str) -> dict[str, Any]:
    full_url = url + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(full_url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Referer": referer,
        "Cookie": cookie,
        "Accept": "application/json, text/plain, */*",
    })
    body = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
    obj = json.loads(body)
    code = obj.get("code")
    if code not in (0, None):
        raise RuntimeError(f"Bilibili API error code={code} message={obj.get('message')} url={full_url}")
    return obj


def bilibili_probe(args: argparse.Namespace) -> int:
    ws=workspace_root(args); acct=account_name(args.account); prof=account_name(args.profile)
    path = Path(args.credential).expanduser().resolve() if args.credential else secret_dir(ws, acct, "bilibili") / f"{prof}.credentials.json"
    creds = load_bilibili_credentials(path)
    cookie = "; ".join(f"{k}={v}" for k,v in creds.items())
    obj = request_json("https://api.bilibili.com/x/web-interface/nav", {}, cookie, "https://www.bilibili.com/")
    data = obj.get("data") or {}
    print(json.dumps({"ok": True, "isLogin": data.get("isLogin"), "mid": data.get("mid"), "uname": data.get("uname"), "level": data.get("level_info",{}).get("current_level")}, ensure_ascii=False, indent=2))
    return 0


def bilibili_summary(args: argparse.Namespace) -> int:
    ws=workspace_root(args); acct=account_name(args.account); prof=account_name(args.profile)
    path = Path(args.credential).expanduser().resolve() if args.credential else secret_dir(ws, acct, "bilibili") / f"{prof}.credentials.json"
    creds = load_bilibili_credentials(path)
    cookie = "; ".join(f"{k}={v}" for k,v in creds.items())
    referer="https://member.bilibili.com/york/data-center-web?tmid=&bvid=&tab="
    fan_obj = request_json("https://member.bilibili.com/x/web/data/v2/overview/stat/graph", {"period": 1, "s_locale": "zh_CN", "type": "fan", "tmid": "", "t": int(time.time()*1000)}, cookie, referer)
    trend=(fan_obj.get("data") or {}).get("tendency") or []
    if not trend: die("no Bilibili fan trend returned")
    latest=max(datetime.fromtimestamp(x["date_key"], TZ).date() for x in trend)
    start=latest-timedelta(days=args.days-1)
    fan_rows=[]
    for x in trend:
        d=datetime.fromtimestamp(x["date_key"], TZ).date()
        if start <= d <= latest:
            fan_rows.append({"date": d.isoformat(), "fan_inc": int(x.get("total_inc") or 0), "sub_total_inc": int(x.get("sub_total_inc") or 0)})
    fan_rows.sort(key=lambda r:r["date"])
    videos=[]
    for pn in range(1, 50):
        obj=request_json("https://member.bilibili.com/x/web/data/archive/index", {"pn":pn,"ps":20,"scene":"archive","order":0,"tmid":"","t":int(time.time()*1000)}, cookie, referer)
        items=(obj.get("data") or {}).get("list") or []
        if not items: break
        for it in items:
            pub=datetime.fromtimestamp(int(it["pubtime"]), TZ)
            if start <= pub.date() <= latest:
                stat=it.get("real_stat") or it.get("stat") or {}
                videos.append({"pubtime": pub.strftime("%Y-%m-%d %H:%M"), "bvid": it.get("bvid"), "title": it.get("title"), "play": int(stat.get("play") or 0), "fans": int(stat.get("fans") or 0), "reply": int(stat.get("reply") or 0), "likes": int(stat.get("likes") or 0), "full_play_ratio": stat.get("full_play_ratio"), "aid": it.get("aid")})
        oldest=datetime.fromtimestamp(int(items[-1]["pubtime"]), TZ).date()
        if oldest < start: break
    videos.sort(key=lambda r:r["pubtime"], reverse=True)
    raw, processed=output_dirs(ws, acct, "bilibili")
    stamp=datetime.now(TZ).strftime("%Y%m%d-%H%M%S")
    result={"account":acct,"platform":"bilibili","source":"Bilibili creator-center APIs","range":{"start":start.isoformat(),"end":latest.isoformat(),"days":args.days},"captured_at":datetime.now(TZ).isoformat(),"fan_total":sum(r["fan_inc"] for r in fan_rows),"fan_rows":fan_rows,"videos":videos}
    jp=raw/f"bilibili-creator-summary-{args.days}d-{stamp}.json"; mp=processed/f"bilibili-creator-summary-{args.days}d-{stamp}.md"
    jp.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    lines=[f"# {acct} Bilibili creator data ({args.days} days)","",f"Range: {start.isoformat()} to {latest.isoformat()}",f"Fan total: {result['fan_total']:,}","","## Daily fans",""]
    lines += [f"- {r['date']}: +{r['fan_inc']:,}" for r in fan_rows]
    lines += ["","## Published videos",""]
    for v in videos:
        lines += [f"- {v['pubtime']} `{v['bvid']}` {v['title']} — play {v['play']:,}, fans {v['fans']:,}, reply {v['reply']:,}, likes {v['likes']:,}"]
    mp.write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(json.dumps({"ok":True,"json":str(jp),"markdown":str(mp),"fan_total":result["fan_total"],"videos":len(videos)},ensure_ascii=False,indent=2))
    return 0


# ---------------- Douyin ----------------

def normalize_cookie(c: dict[str, Any]) -> dict[str, Any]:
    nc={k:v for k,v in c.items() if k in {"name","value","domain","path","expires","httpOnly","secure","sameSite"}}
    if "path" not in nc or not nc["path"]: nc["path"]="/"
    if "sameSite" in nc:
        s=str(nc["sameSite"]).lower(); nc["sameSite"]="Strict" if s=="strict" else "None" if s=="none" else "Lax"
    if nc.get("expires") in (None,"",0): nc.pop("expires",None)
    return nc


def douyin_check_cookies(args: argparse.Namespace) -> int:
    ws=workspace_root(args); acct=account_name(args.account); prof=account_name(args.profile)
    path = Path(args.cookies).expanduser().resolve() if args.cookies else secret_dir(ws, acct, "douyin") / f"{prof}.cookies.json"
    if not path.exists(): die(f"missing Douyin cookie json: {path}")
    data=json.loads(path.read_text(encoding="utf-8")); cookies=data if isinstance(data,list) else data.get("cookies") if isinstance(data,dict) else None
    if not isinstance(cookies,list): die("Cookie-Editor export must be a JSON list or a dict with cookies")
    domains=sorted({str(c.get("domain","")) for c in cookies if isinstance(c,dict)})
    names={str(c.get("name","")) for c in cookies if isinstance(c,dict)}
    important=[n for n in ["sessionid","sessionid_ss","sid_guard","uid_tt","uid_tt_ss","passport_csrf_token"] if n in names]
    print(json.dumps({"ok":True,"path":str(path),"cookie_count":len(cookies),"domains":domains,"important_names_present":important},ensure_ascii=False,indent=2))
    return 0


async def _import_douyin_cookies_async(args: argparse.Namespace) -> dict[str, Any]:
    from playwright.async_api import async_playwright
    ws=workspace_root(args); acct=account_name(args.account); prof=account_name(args.profile)
    path = Path(args.cookies).expanduser().resolve() if args.cookies else secret_dir(ws, acct, "douyin") / f"{prof}.cookies.json"
    data=json.loads(path.read_text(encoding="utf-8")); cookies=data if isinstance(data,list) else data.get("cookies")
    if not isinstance(cookies,list): die("Cookie-Editor export must be a JSON list or dict with cookies")
    sd=secret_dir(ws, acct, "douyin"); sd.mkdir(parents=True,exist_ok=True)
    state=sd / f"{prof}.storage_state.json"
    async with async_playwright() as p:
        browser=await p.chromium.launch(executable_path=args.chromium, headless=args.headless, args=["--no-sandbox","--disable-dev-shm-usage","--disable-blink-features=AutomationControlled"])
        ctx=await browser.new_context(viewport={"width":1365,"height":900}, locale="zh-CN", timezone_id="Asia/Shanghai")
        await ctx.add_cookies([normalize_cookie(c) for c in cookies])
        page=await ctx.new_page()
        await page.goto("https://creator.douyin.com/creator-micro/home", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(8000)
        body=(await page.locator("body").inner_text(timeout=5000))[:3000]
        await ctx.storage_state(path=str(state))
        await browser.close()
    return {"ok": True, "storage_state": str(state), "login_page_hint": any(x in body for x in ["扫码登录","验证码登录","登录/注册"]), "account_hints": {"douyin_id": args.douyin_id or "", "douyin_id_seen": bool(args.douyin_id and args.douyin_id in body), "nickname": args.nickname or "", "nickname_seen": bool(args.nickname and args.nickname in body)}}


def douyin_import_cookies(args: argparse.Namespace) -> int:
    print(json.dumps(asyncio.run(_import_douyin_cookies_async(args)),ensure_ascii=False,indent=2)); return 0


def pick(obj: dict[str, Any], *names: str) -> Any:
    for n in names:
        if isinstance(obj,dict) and n in obj and obj[n] not in (None,""):
            return obj[n]
    return None


def ts_to_str(ts: Any) -> str:
    try:
        t=int(ts); t = t//1000 if t>10_000_000_000 else t
        return datetime.fromtimestamp(t,TZ).strftime("%Y-%m-%d %H:%M:%S")
    except Exception: return ""


def normalize_aweme(a: dict[str, Any]) -> dict[str, Any]:
    stat=a.get("Statistics") or a.get("statistics") or {}
    item={"aweme_id": pick(a,"AwemeId","aweme_id","item_id","id"), "title": pick(a,"Desc","desc","Title","title") or "", "create_time": pick(a,"CreateTime","create_time"), "create_time_str":"", "duration": pick(a,"Duration","duration"), "cover":"", "url":""}
    item["create_time_str"]=ts_to_str(item["create_time"])
    cover=pick(a,"Cover","cover")
    if isinstance(cover,dict):
        urls=cover.get("url_list") or cover.get("UrlList") or []; item["cover"]=urls[0] if urls else cover.get("uri","")
    for src in [a, stat]:
        if not isinstance(src,dict): continue
        for out,names in {"play":["PlayCnt","play_count","play","view_count"],"like":["DiggCnt","digg_count","like_count"],"comment":["CommentCnt","comment_count"],"share":["ShareCnt","share_count"],"collect":["CollectCnt","collect_count"],"forward":["ForwardCnt","forward_count"]}.items():
            if out not in item or item[out] in (None,""):
                v=pick(src,*names)
                if v is not None: item[out]=v
    if item.get("aweme_id"): item["url"]=f"https://www.douyin.com/video/{item['aweme_id']}"
    return item


async def _douyin_worklist_async(args: argparse.Namespace) -> dict[str, Any]:
    from playwright.async_api import async_playwright
    ws=workspace_root(args); acct=account_name(args.account); prof=account_name(args.profile)
    state = Path(args.storage_state).expanduser().resolve() if args.storage_state else secret_dir(ws, acct, "douyin") / f"{prof}.storage_state.json"
    if not state.exists(): die(f"missing Douyin storage state; run import-douyin-cookies first: {state}")
    async with async_playwright() as p:
        browser=await p.chromium.launch(executable_path=args.chromium, headless=args.headless, args=["--no-sandbox","--disable-dev-shm-usage","--disable-blink-features=AutomationControlled","--window-size=1365,900"])
        ctx=await browser.new_context(viewport={"width":1365,"height":900}, locale="zh-CN", timezone_id="Asia/Shanghai", storage_state=str(state))
        page=await ctx.new_page(); await page.goto("https://creator.douyin.com/creator-micro/content/manage", wait_until="domcontentloaded", timeout=60000); await page.wait_for_timeout(5000)
        all_pages=[]; all_items=[]; cursor=0
        for pn in range(1,args.max_pages+1):
            url=f"/janus/douyin/creator/pc/work_list?scene=star_atlas&device_platform=android&aid=1128&status=0&count=12&max_cursor={cursor}"
            obj=await page.evaluate("""async (url) => { const r=await fetch(url,{credentials:'same-origin'}); const text=await r.text(); try { return {status:r.status,json:JSON.parse(text)}; } catch(e) { return {status:r.status,textPrefix:text.slice(0,1000),parseError:String(e)}; } }""", url)
            js=obj.get("json") or {}; aw=js.get("aweme_list") or []
            all_pages.append({"pn":pn,"cursor":cursor,"status":obj.get("status"),"count":len(aw),"has_more":js.get("has_more"),"max_cursor":js.get("max_cursor"),"raw":js})
            all_items.extend(aw)
            nxt=js.get("max_cursor") or js.get("cursor") or js.get("next_cursor"); has_more=js.get("has_more")
            if not aw or not has_more or nxt in (None,"",cursor): break
            cursor=nxt; await page.wait_for_timeout(1000)
        await browser.close()
    seen=set(); items=[]
    for a in all_items:
        n=normalize_aweme(a); key=n.get("aweme_id") or json.dumps(a,ensure_ascii=False)[:100]
        if key in seen: continue
        seen.add(key); items.append(n)
    cutoff=(datetime.now(TZ)-timedelta(days=args.days)).date() if args.days else None
    recent=[]
    for n in items:
        try:
            d=datetime.fromtimestamp(int(n["create_time"]),TZ).date()
            if cutoff is None or d>=cutoff: recent.append(n)
        except Exception: pass
    items.sort(key=lambda x:int(x.get("create_time") or 0), reverse=True); recent.sort(key=lambda x:int(x.get("create_time") or 0), reverse=True)
    raw,processed=output_dirs(ws,acct,"douyin"); stamp=datetime.now(TZ).strftime("%Y%m%d-%H%M%S")
    result={"account":acct,"platform":"douyin","source":"Douyin creator center /janus/douyin/creator/pc/work_list","captured_at":datetime.now(TZ).isoformat(),"range":{"days":args.days,"cutoff":cutoff.isoformat() if cutoff else None},"page_count":len(all_pages),"item_count":len(items),"recent_count":len(recent),"pages":all_pages,"items":items,"recent_items":recent}
    jp=raw/f"douyin-creator-worklist-{args.days or 'all'}d-{stamp}.json"; mp=processed/f"douyin-creator-worklist-{args.days or 'all'}d-{stamp}.md"
    jp.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    def fmt(v:Any)->str:
        if v in (None,""): return ""
        try: return f"{int(v):,}"
        except Exception: return str(v)
    rows=recent if args.days else items
    lines=[f"# {acct} Douyin creator worklist ({args.days or 'all'} days)","",f"Captured at: {result['captured_at']}",f"Items returned: {len(items)}; selected: {len(rows)}","","| Time | Work ID | Title/desc | Play | Like | Comment | Share | Collect |","|---|---|---|---:|---:|---:|---:|---:|"]
    for it in rows:
        title=(it.get("title") or "").replace("|","/").replace("\n"," ")[:90]
        lines.append(f"| {it.get('create_time_str','')} | {it.get('aweme_id','')} | {title} | {fmt(it.get('play'))} | {fmt(it.get('like'))} | {fmt(it.get('comment'))} | {fmt(it.get('share'))} | {fmt(it.get('collect'))} |")
    mp.write_text("\n".join(lines)+"\n",encoding="utf-8")
    return {"ok":True,"json":str(jp),"markdown":str(mp),"items":len(items),"selected":len(rows),"pages":len(all_pages)}


def douyin_worklist(args: argparse.Namespace) -> int:
    print(json.dumps(asyncio.run(_douyin_worklist_async(args)),ensure_ascii=False,indent=2)); return 0


def parse_args() -> argparse.Namespace:
    p=argparse.ArgumentParser(description="Read-only Bilibili/Douyin creator data collector")
    p.add_argument("command", choices=["init-account","bilibili-probe","bilibili-summary","check-douyin-cookies","import-douyin-cookies","douyin-worklist"])
    p.add_argument("--workspace", default=str(Path.cwd()))
    p.add_argument("--account", required=True, help="Account/business namespace, e.g. xgame or colleague-a")
    p.add_argument("--profile", default="default")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--credential", default="")
    p.add_argument("--cookies", default="")
    p.add_argument("--storage-state", default="")
    p.add_argument("--chromium", default="/usr/bin/chromium")
    p.add_argument("--headless", action="store_true")
    p.add_argument("--max-pages", type=int, default=20)
    p.add_argument("--douyin-id", default="", help="Optional expected Douyin ID for login verification")
    p.add_argument("--nickname", default="", help="Optional expected nickname for login verification")
    return p.parse_args()


def main() -> int:
    args=parse_args(); account_name(args.account); account_name(args.profile)
    return {
        "init-account": init_account,
        "bilibili-probe": bilibili_probe,
        "bilibili-summary": bilibili_summary,
        "check-douyin-cookies": douyin_check_cookies,
        "import-douyin-cookies": douyin_import_cookies,
        "douyin-worklist": douyin_worklist,
    }[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
