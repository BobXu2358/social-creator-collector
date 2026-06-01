"""Single entry point: ``python -m collector <platform> <action> ...``.

Never prints cookie/token values. Outputs a JSON result line per command.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__, bilibili, douyin
from .paths import CollectorError, output_dirs, safe_name, secret_dir, workspace_root


def _bounded_int(min_v: int, max_v: int | None = None):
    """argparse ``type`` that rejects out-of-range ints up front.

    Stops a stray ``--max-pages 999999`` (or a negative delay) from turning a
    read-only collect into a risk-control magnet before a single request goes out.
    """
    def _parse(s: str) -> int:
        try:
            v = int(s)
        except ValueError:
            raise argparse.ArgumentTypeError(f"expected an integer, got {s!r}")
        if v < min_v or (max_v is not None and v > max_v):
            hi = max_v if max_v is not None else "∞"
            raise argparse.ArgumentTypeError(f"must be in [{min_v}, {hi}], got {v}")
        return v
    return _parse


# ── path resolution ──────────────────────────────────────────────────────

def _ws(args) -> Path:
    return workspace_root(args.workspace)


def _bili_credential(args) -> Path:
    if args.credential:
        return Path(args.credential).expanduser().resolve()
    return secret_dir(_ws(args), args.account, "bilibili") / f"{safe_name(args.profile)}.credentials.json"


def _douyin_cookies(args) -> Path:
    if args.cookies:
        return Path(args.cookies).expanduser().resolve()
    return secret_dir(_ws(args), args.account, "douyin") / f"{safe_name(args.profile)}.cookies.json"


def _douyin_state(args) -> Path:
    if getattr(args, "storage_state", ""):
        return Path(args.storage_state).expanduser().resolve()
    return secret_dir(_ws(args), args.account, "douyin") / f"{safe_name(args.profile)}.storage_state.json"


def _sessdata(args) -> str | None:
    if args.sessdata:
        return args.sessdata
    path = _bili_credential(args)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8")).get("SESSDATA")
    return None


# ── init ─────────────────────────────────────────────────────────────────

def _init(args) -> dict[str, Any]:
    ws, account = _ws(args), safe_name(args.account, kind="account")
    created: list[str] = []
    for platform in ("bilibili", "douyin"):
        sd = secret_dir(ws, account, platform)
        sd.mkdir(parents=True, exist_ok=True)
        output_dirs(ws, account, platform)
        created.append(str(sd))
    bili_example = secret_dir(ws, account, "bilibili") / "default.credentials.example.json"
    if not bili_example.exists():
        bili_example.write_text(json.dumps(
            {"SESSDATA": "paste_here", "bili_jct": "paste_here", "buvid3": "paste_here"},
            ensure_ascii=False, indent=2), encoding="utf-8")
        created.append(str(bili_example))
    douyin_example = secret_dir(ws, account, "douyin") / "default.cookies.example.json"
    if not douyin_example.exists():
        douyin_example.write_text("[]\n", encoding="utf-8")
        created.append(str(douyin_example))
    return {"ok": True, "account": account, "created": created}


# ── argparse ─────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="collector",
                                description="Read-only Bilibili/Douyin creator-data collector")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    groups = p.add_subparsers(dest="group", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace", default="", help="workspace root (default: cwd)")
    common.add_argument("--account", required=True, help="account/business namespace, e.g. xgame")
    common.add_argument("--profile", default="default", help="credential profile name")
    common.add_argument("--debug", action="store_true",
                        help="on unexpected error, print a full traceback instead of one-line ERROR")

    def _chromium(parser):
        parser.add_argument("--chromium", default="",
                            help="Chromium binary path (default: Playwright bundled)")

    # init
    g_init = groups.add_parser("init", parents=[common], help="create account folder structure")
    g_init.set_defaults(func=_init)

    # bilibili
    g_bili = groups.add_parser("bilibili", help="Bilibili (httpx) commands")
    bili = g_bili.add_subparsers(dest="action", required=True)

    b_login = bili.add_parser("login", parents=[common],
                              help="QR scan login (headed browser) → credential file")
    b_login.add_argument("--credential", default="")
    b_login.add_argument("--timeout", type=_bounded_int(1, 3600), default=180, dest="timeout_s")
    _chromium(b_login)
    b_login.set_defaults(func=lambda a: bilibili.login(
        ws=_ws(a), account=a.account, credential_path=_bili_credential(a),
        chromium=a.chromium or None, timeout_s=a.timeout_s))

    b_probe = bili.add_parser("probe", parents=[common], help="verify B站 login + identity")
    b_probe.add_argument("--credential", default="")
    b_probe.set_defaults(func=lambda a: bilibili.probe(
        ws=_ws(a), account=a.account, credential_path=_bili_credential(a)))

    b_sum = bili.add_parser("summary", parents=[common], help="fan trend + per-video metrics")
    b_sum.add_argument("--credential", default="")
    b_sum.add_argument("--days", type=_bounded_int(1, 3650), default=30)
    b_sum.set_defaults(func=lambda a: bilibili.summary(
        ws=_ws(a), account=a.account, credential_path=_bili_credential(a), days=a.days))

    b_cmt = bili.add_parser("comments", parents=[common], help="collect video comments")
    b_cmt.add_argument("--credential", default="")
    src = b_cmt.add_mutually_exclusive_group(required=True)
    src.add_argument("--bvid")
    src.add_argument("--aid", type=int)
    b_cmt.add_argument("--sessdata", default="")
    b_cmt.add_argument("--max-pages", type=_bounded_int(1, 500), default=10, dest="max_pages")
    b_cmt.add_argument("--delay-ms", type=_bounded_int(0, 60000), default=600, dest="delay_ms")
    b_cmt.set_defaults(func=lambda a: bilibili.comments(
        ws=_ws(a), account=a.account, bvid=a.bvid, aid=a.aid, sessdata=_sessdata(a),
        max_pages=a.max_pages, delay_ms=a.delay_ms))

    b_dm = bili.add_parser("danmaku", parents=[common], help="fetch + analyze danmaku")
    dm_src = b_dm.add_mutually_exclusive_group(required=True)
    dm_src.add_argument("--bvid")
    dm_src.add_argument("--cid", type=int)
    b_dm.add_argument("--bucket-s", type=_bounded_int(1, 3600), default=10, dest="bucket_s")
    b_dm.add_argument("--peak-n", type=_bounded_int(1, 100), default=5, dest="peak_n")
    b_dm.add_argument("--peak-method", choices=["topn", "zscore"], default="topn", dest="peak_method")
    b_dm.add_argument("--no-filter", action="store_true", dest="no_filter",
                      help="keep subtitle danmaku (pool=1)")
    b_dm.set_defaults(func=lambda a: bilibili.danmaku(
        ws=_ws(a), account=a.account, bvid=a.bvid, cid=a.cid, bucket_s=a.bucket_s,
        peak_n=a.peak_n, peak_method=a.peak_method, filter_pool1=not a.no_filter))

    # douyin
    g_dy = groups.add_parser("douyin", help="Douyin (Playwright) commands")
    dy = g_dy.add_subparsers(dest="action", required=True)

    d_login = dy.add_parser("login", parents=[common],
                            help="QR scan login (headed browser) → storage state")
    d_login.add_argument("--storage-state", default="", dest="storage_state")
    d_login.add_argument("--timeout", type=_bounded_int(1, 3600), default=180, dest="timeout_s")
    _chromium(d_login)
    d_login.set_defaults(func=lambda a: douyin.login(
        ws=_ws(a), account=a.account, state_path=_douyin_state(a),
        chromium=a.chromium or None, timeout_s=a.timeout_s))

    d_chk = dy.add_parser("check-cookies", parents=[common], help="validate Cookie-Editor export")
    d_chk.add_argument("--cookies", default="")
    d_chk.set_defaults(func=lambda a: douyin.check_cookies(path=_douyin_cookies(a)))

    d_imp = dy.add_parser("import-cookies", parents=[common],
                          help="import cookies → storage state + verify login")
    d_imp.add_argument("--cookies", default="")
    d_imp.add_argument("--nickname", default="")
    d_imp.add_argument("--douyin-id", default="", dest="douyin_id")
    _chromium(d_imp)
    d_imp.set_defaults(func=lambda a: douyin.import_cookies(
        cookies_path=_douyin_cookies(a), state_path=_douyin_state(a), chromium=a.chromium or None,
        nickname=a.nickname or None, douyin_id=a.douyin_id or None))

    d_wl = dy.add_parser("worklist", parents=[common], help="creator-center work list")
    d_wl.add_argument("--storage-state", default="", dest="storage_state")
    d_wl.add_argument("--days", type=_bounded_int(0, 3650), default=30)
    d_wl.add_argument("--max-pages", type=_bounded_int(1, 500), default=20, dest="max_pages")
    _chromium(d_wl)
    d_wl.set_defaults(func=lambda a: douyin.worklist(
        ws=_ws(a), account=a.account, state_path=_douyin_state(a), days=a.days,
        max_pages=a.max_pages, chromium=a.chromium or None))

    d_fg = dy.add_parser("fan-growth", parents=[common],
                         help="per-video fan growth (粉丝增量) from DOM")
    d_fg.add_argument("--storage-state", default="", dest="storage_state")
    d_fg.add_argument("--max-scroll", type=_bounded_int(1, 500), default=40, dest="max_scroll",
                      help="max scroll rounds to lazy-load the 投稿列表")
    _chromium(d_fg)
    d_fg.set_defaults(func=lambda a: douyin.fan_growth(
        ws=_ws(a), account=a.account, state_path=_douyin_state(a), chromium=a.chromium or None,
        max_scroll=a.max_scroll))

    d_cmt = dy.add_parser("comments", parents=[common], help="collect video comments")
    d_cmt.add_argument("--storage-state", default="", dest="storage_state")
    d_cmt.add_argument("--aweme-id", required=True, dest="aweme_id")
    d_cmt.add_argument("--max-pages", type=_bounded_int(1, 500), default=20, dest="max_pages")
    _chromium(d_cmt)
    d_cmt.set_defaults(func=lambda a: douyin.comments(
        ws=_ws(a), account=a.account, aweme_id=a.aweme_id, state_path=_douyin_state(a),
        max_pages=a.max_pages, chromium=a.chromium or None))

    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result = args.func(args)
    except CollectorError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # last-resort guard: never dump a raw traceback at users
        if getattr(args, "debug", False):
            raise
        print(f"ERROR: unexpected {type(exc).__name__}: {exc}", file=sys.stderr)
        print("       re-run with --debug for the full traceback", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
