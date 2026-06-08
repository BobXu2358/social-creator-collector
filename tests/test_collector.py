"""Offline tests: path isolation, name validation, CLI error paths, pure parsers.

No network and no browser — everything here runs in CI without cookies. The live
collection paths are verified manually (see AGENTS.md), not here.
"""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import httpx

from collector import bilibili, douyin, schema
from collector.cli import main
from collector.paths import CollectorError, output_dirs, safe_name, secret_dir, workspace_root

try:
    import jsonschema
except ImportError:  # optional dev dep — schema-conformance tests skip without it
    jsonschema = None

_SCHEMA = json.loads(
    (Path(__file__).resolve().parent.parent / "schemas" / "collector-output.schema.json")
    .read_text(encoding="utf-8")
)


class NameValidation(unittest.TestCase):
    def test_accepts_simple_names(self):
        for name in ("xgame", "chaping-jun", "acct_1", "a.b"):
            self.assertEqual(safe_name(name), name)

    def test_rejects_traversal_and_separators(self):
        for bad in ("../chapingjun", "a/b", "..", "", "x/../y", "with space"):
            with self.assertRaises(CollectorError):
                safe_name(bad)


class PathIsolation(unittest.TestCase):
    def test_secret_and_output_dirs_are_account_scoped(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = workspace_root(tmp)
            sd = secret_dir(ws, "xgame", "douyin")
            raw, processed = output_dirs(ws, "xgame", "bilibili")
            # as_posix() so the assertions hold on Windows too (str(Path) uses '\').
            self.assertTrue(sd.as_posix().endswith("/social/_secrets/xgame/douyin"))
            self.assertIn("/social/xgame/bilibili/raw", raw.as_posix())
            self.assertIn("/social/xgame/bilibili/processed", processed.as_posix())
            self.assertNotIn("chapingjun", str(sd) + str(raw))
            self.assertTrue(raw.is_dir() and processed.is_dir())

    def test_secret_dir_rejects_traversal_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(CollectorError):
                secret_dir(workspace_root(tmp), "../other", "bilibili")


def _run(*argv) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as exc:  # argparse errors
            code = int(exc.code or 0)
    return code, out.getvalue(), err.getvalue()


class CliBehavior(unittest.TestCase):
    def test_init_creates_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, _ = _run("init", "--account", "xgame", "--workspace", tmp)
            self.assertEqual(code, 0)
            payload = json.loads(out)
            self.assertEqual(payload["account"], "xgame")
            self.assertTrue((Path(tmp) / "social" / "_secrets" / "xgame" / "bilibili").is_dir())
            self.assertTrue((Path(tmp) / "social" / "xgame" / "douyin" / "raw").is_dir())

    def test_missing_account_is_rejected(self):
        code, _, err = _run("bilibili", "probe")
        self.assertNotEqual(code, 0)
        self.assertIn("--account", err)

    def test_bilibili_probe_missing_credential_fails_loud(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _, err = _run("bilibili", "probe", "--account", "xgame", "--workspace", tmp)
            self.assertEqual(code, 2)
            self.assertIn("missing Bilibili credential", err)

    def test_bilibili_load_credentials_accepts_legacy_without_buvid3(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bili.json"
            path.write_text(json.dumps({
                "SESSDATA": "sess",
                "bili_jct": "csrf",
                "DedeUserID": "123",
                "sid": "abc",
            }), encoding="utf-8")
            creds = bilibili.load_credentials(path)
        self.assertEqual(creds["SESSDATA"], "sess")
        self.assertEqual(creds["bili_jct"], "csrf")
        self.assertEqual(creds["DedeUserID"], "123")
        self.assertEqual(creds["sid"], "abc")
        self.assertNotIn("buvid3", creds)

    def test_bilibili_load_credentials_accepts_cookie_editor_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bili-cookies.json"
            path.write_text(json.dumps([
                {"name": "SESSDATA", "value": "sess", "domain": ".bilibili.com"},
                {"name": "bili_jct", "value": "csrf", "domain": ".bilibili.com"},
                {"name": "buvid3", "value": "device", "domain": ".bilibili.com"},
                {"name": "ignored_empty", "value": ""},
            ]), encoding="utf-8")
            creds = bilibili.load_credentials(path)
        self.assertEqual(creds, {"SESSDATA": "sess", "bili_jct": "csrf", "buvid3": "device"})

    def test_bilibili_load_credentials_still_requires_login_cookies(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bili.json"
            path.write_text(json.dumps({"SESSDATA": "sess"}), encoding="utf-8")
            with self.assertRaises(CollectorError) as ctx:
                bilibili.load_credentials(path)
        self.assertIn("bili_jct", str(ctx.exception))

    def test_bilibili_comments_without_cookie_fails_loud(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _, err = _run("bilibili", "comments", "--account", "xgame",
                                 "--bvid", "BV1xx", "--workspace", tmp)
            self.assertEqual(code, 2)
            self.assertIn("login cookie", err)

    def test_douyin_worklist_missing_state_fails_loud(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _, err = _run("douyin", "worklist", "--account", "xgame", "--workspace", tmp)
            self.assertEqual(code, 2)
            self.assertIn("storage state", err)

    def test_login_bad_chromium_fails_loud_without_hanging(self):
        # A nonexistent browser binary makes the headed launch fail immediately —
        # no window, no scan — so login's error path is testable offline.
        for platform in ("bilibili", "douyin"):
            with tempfile.TemporaryDirectory() as tmp:
                code, _, err = _run(platform, "login", "--account", "xgame", "--workspace", tmp,
                                    "--chromium", "/nonexistent/chromium-binary", "--timeout", "1")
                self.assertEqual(code, 2, f"{platform}: {err}")
                self.assertIn("ERROR", err)


class PureParsers(unittest.TestCase):
    def test_parse_fan_growth_int(self):
        self.assertEqual(douyin._parse_int("2,383"), 2383)
        self.assertEqual(douyin._parse_int("+76"), 76)
        self.assertEqual(douyin._parse_int("-12"), -12)
        self.assertIsNone(douyin._parse_int(""))
        self.assertIsNone(douyin._parse_int("—"))

    def test_normalize_cookie_samesite_and_path(self):
        c = douyin._normalize_cookie({"name": "x", "value": "y", "sameSite": "no_restriction", "extra": 1})
        self.assertEqual(c["sameSite"], "None")
        self.assertEqual(c["path"], "/")
        self.assertNotIn("extra", c)
        self.assertEqual(douyin._normalize_cookie({"name": "x", "value": "y", "sameSite": "none"})["sameSite"], "None")

    def test_check_cookies_does_not_expose_domain_list(self):
        cookies = [
            {"name": "sessionid", "value": "x", "domain": ".douyin.com"},
            {"name": "unrelated", "value": "y", "domain": ".example.com"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cookies.json"
            path.write_text(json.dumps(cookies), encoding="utf-8")
            result = douyin.check_cookies(path=path)
        self.assertEqual(result["douyin_domain_cookie_count"], 1)
        self.assertEqual(result["other_domain_cookie_count"], 1)
        self.assertEqual(result["important_names_present"], ["sessionid"])
        self.assertNotIn("domains", result)

    def test_import_cookie_verification_requires_positive_signal(self):
        login_body = "请扫码登录后继续"
        self.assertFalse(douyin._import_cookie_verification(
            login_body, {"status": 200, "json": {}}, nickname=None, douyin_id=None,
        )["ok"])
        self.assertTrue(douyin._import_cookie_verification(
            "作品管理", {"status": 200, "json": {}}, nickname=None, douyin_id=None,
        )["ok"])
        self.assertTrue(douyin._import_cookie_verification(
            "", {"status": 200, "json": {"aweme_list": [], "has_more": False}},
            nickname=None, douyin_id=None,
        )["ok"])
        failed_api = douyin._import_cookie_verification(
            "", {"status": 200, "json": {"status_code": 8, "status_msg": "not login"}},
            nickname=None, douyin_id=None,
        )
        self.assertFalse(failed_api["ok"])
        self.assertEqual(failed_api["api_error"], {"status_code": 8, "status_msg": "not login"})

    def test_analyze_danmaku_finds_peak(self):
        # 30 danmaku clustered at t=100s, a few scattered → peak bucket at 100.
        dms = [{"time_s": 100 + i * 0.1, "pool": 0, "content": "笑死 这段太好笑"} for i in range(30)]
        dms += [{"time_s": float(t), "pool": 0, "content": "前排"} for t in (5, 40, 220)]
        analysis = bilibili.analyze_danmaku(dms, title="t", bucket_s=10)
        self.assertEqual(analysis["total_danmaku"], 33)
        self.assertTrue(analysis["peaks"])
        self.assertEqual(analysis["peaks"][0]["start_s"], 100)
        self.assertTrue(any("笑死" in kw or "好笑" in kw for kw, _ in analysis["peaks"][0]["keywords"]))

    def test_analyze_danmaku_empty(self):
        self.assertEqual(bilibili.analyze_danmaku([], title="t")["total_danmaku"], 0)

    def test_analyze_danmaku_uses_real_video_duration(self):
        # danmaku stop ~103s, but the video is 600s — density must use 600, not 103.
        dms = [{"time_s": 100 + i * 0.1, "pool": 0, "content": "x"} for i in range(30)]
        with_dur = bilibili.analyze_danmaku(dms, video_duration_s=600)
        self.assertEqual(with_dur["duration_s"], 600)
        self.assertEqual(with_dur["density_per_min"], 3.0)  # 30 / (600/60)
        # without it, falls back to the last danmaku timestamp (old behaviour)
        self.assertLess(bilibili.analyze_danmaku(dms)["duration_s"], 110)

    def test_decode_danmaku_segment_payload(self):
        def varint(v: int) -> bytes:
            out = bytearray()
            while True:
                b = v & 0x7F
                v >>= 7
                out.append(b | 0x80 if v else b)
                if not v:
                    return bytes(out)

        def field_varint(field: int, value: int) -> bytes:
            return varint((field << 3) | 0) + varint(value)

        def field_bytes(field: int, value: bytes) -> bytes:
            return varint((field << 3) | 2) + varint(len(value)) + value

        elem = b"".join([
            field_varint(2, 1234),                         # progress ms
            field_varint(3, 1),                            # mode
            field_bytes(7, "A & B <tag> 'q'".encode()),    # content
            field_varint(11, 0),                           # pool
        ])
        payload = field_bytes(1, elem)

        class _Resp:
            status_code = 200

            def __init__(self, content):
                self.content = content

            def raise_for_status(self):
                pass

        class _Client:
            def __init__(self):
                self.contents = [payload, b""]
                self.calls = 0

            def get(self, url, params=None):
                self.calls += 1
                return _Resp(self.contents.pop(0))

        client = _Client()
        dms = bilibili.fetch_danmaku(client, cid=1)
        self.assertEqual(len(dms), 1)
        self.assertEqual(client.calls, 2)
        self.assertEqual(dms[0]["time_s"], 1.234)
        self.assertEqual(dms[0]["type"], 1)
        self.assertEqual(dms[0]["pool"], 0)
        self.assertEqual(dms[0]["content"], "A & B <tag> 'q'")

    def test_fetch_danmaku_treats_304_as_segment_end(self):
        class _Resp:
            status_code = 304
            content = b""

            def raise_for_status(self):
                raise AssertionError("304 should stop before raise_for_status")

        class _Client:
            def get(self, url, params=None):
                return _Resp()

        self.assertEqual(bilibili.fetch_danmaku(_Client(), cid=1), [])

    def test_date_from_epoch_rejects_bad_values(self):
        self.assertIsNone(bilibili._date_from_epoch(None))
        self.assertIsNone(bilibili._date_from_epoch("bad"))
        self.assertIsNotNone(bilibili._date_from_epoch(1716000000))

    def test_bilibili_stat_int_falls_back_across_sources(self):
        self.assertEqual(bilibili._stat_int({"play": "123"}, names=("play",)), 123)
        self.assertEqual(bilibili._stat_int({"coin": ""}, {"coin": 456}, names=("coin",)), 456)
        self.assertEqual(bilibili._stat_int({"likes": 7}, names=("like", "likes")), 7)
        self.assertEqual(bilibili._stat_int({"coin": "bad"}, names=("coin",)), 0)

    def test_bilibili_fan_source_rows(self):
        rows = bilibili._fan_source_rows({"video": 60, "search": 30, "other": 10, "bad": "x"})
        self.assertEqual([r["source_key"] for r in rows], ["video", "search", "other"])
        self.assertEqual(rows[0]["source_label"], "video")
        self.assertEqual(rows[0]["count"], 60)
        self.assertEqual(rows[0]["share_pct"], 60.0)

    def test_bilibili_duration_seconds_parses_colon_format(self):
        self.assertEqual(bilibili._duration_seconds("10:05"), 605)
        self.assertEqual(bilibili._duration_seconds("01:02:03"), 3723)
        self.assertIsNone(bilibili._duration_seconds("bad"))

    def test_parse_fan_table_locates_column_by_header(self):
        table = [
            ["作品", "播放量", "粉丝增量", "评论"],
            ["视频一\n2026-05-20 12:00", "1,234", "+76", "5"],
            ["视频二\n2026-05-18 09:30", "999", "-3", "0"],
            ["", "x", "y", "z"],  # empty first cell → skipped
        ]
        rows = douyin._parse_fan_table(table)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0], {"title": "视频一", "published": "2026-05-20 12:00",
                                   "fan_growth_raw": "+76", "fan_growth": 76})
        self.assertEqual(rows[1]["fan_growth"], -3)
        # no 粉丝增量 header anywhere → empty (the caller fails loud, not the parser)
        self.assertEqual(douyin._parse_fan_table([["播放量", "评论"], ["1", "2"]]), [])

    def test_fan_growth_canonical_adds_fallback_join_key(self):
        captured = "2026-06-02T12:00:00+08:00"
        parsed = {"title": "视频一", "published": "2026-05-20 12:00", "fan_growth": 76}
        row = douyin._fan_growth_canonical(parsed, "xgame", captured)
        self.assertIsNone(row["content_id"])
        self.assertEqual(row["title"], "视频一")
        self.assertEqual(row["published_at"], "2026-05-20 12:00")
        self.assertEqual(row["metrics"], {"fans": 76})
        self.assertTrue(row["join_key"].startswith("title-published:"))
        self.assertEqual(row["join_key"], douyin._fan_growth_join_key("视频一", "2026-05-20 12:00"))

    def test_normalize_aweme_camelcase_and_snakecase(self):
        camel = douyin._normalize_aweme({
            "AwemeId": 123, "Desc": "标题", "CreateTime": 1716000000,
            "Duration": 180123,
            "Cover": {"UrlList": ["https://img.example/cover.jpg"]},
            "AwemeType": 4,
            "Status": 2,
            "Visibility": "public",
            "AuditStatus": "pass",
            "Statistics": {"PlayCnt": 1000, "DiggCnt": 50, "CommentCnt": 7,
                           "ShareCnt": 2, "ForwardCnt": 0, "CollectCnt": 3},
        })
        self.assertEqual(camel["aweme_id"], 123)
        self.assertEqual(camel["title"], "标题")
        self.assertEqual((camel["play"], camel["like"], camel["comment"],
                          camel["share"], camel["collect"]), (1000, 50, 7, 2, 3))
        self.assertEqual(camel["duration_s"], 180.123)
        self.assertEqual(camel["cover_url"], "https://img.example/cover.jpg")
        self.assertEqual((camel["work_type"], camel["status"], camel["visibility"], camel["audit_status"]),
                         (4, 2, "public", "pass"))
        self.assertEqual(camel["forward"], 0)
        self.assertEqual(camel["url"], "https://www.douyin.com/video/123")
        snake = douyin._normalize_aweme({"aweme_id": "456", "desc": "t2",
                                         "create_time": 1716000000, "duration": 90,
                                         "cover": ["https://img.example/cover2.jpg"],
                                         "play_count": 9, "digg_count": 1})
        self.assertEqual((snake["aweme_id"], snake["play"], snake["like"]), ("456", 9, 1))
        self.assertEqual(snake["duration_s"], 90)
        self.assertEqual(snake["cover_url"], "https://img.example/cover2.jpg")

    def test_aweme_canonical_preserves_work_metadata(self):
        captured = "2026-06-02T12:00:00+08:00"
        row = douyin._aweme_canonical({
            "aweme_id": "123",
            "title": "t",
            "create_time": 1716000000,
            "url": "https://www.douyin.com/video/123",
            "play": 10,
            "share": 4,
            "forward": 0,
            "duration_s": 180.123,
            "cover_url": "https://img.example/cover.jpg",
            "work_type": 4,
            "status": 2,
            "visibility": "public",
            "audit_status": "pass",
        }, "xgame", captured)
        self.assertEqual(row["duration_s"], 180.123)
        self.assertEqual(row["cover_url"], "https://img.example/cover.jpg")
        self.assertEqual(row["metrics"]["shares"], 4)
        self.assertEqual(row["platform_fields"], {"forward": 0})
        self.assertEqual((row["work_type"], row["status"], row["visibility"], row["audit_status"]),
                         (4, 2, "public", "pass"))

    def test_worklist_empty_diagnostics_helpers(self):
        self.assertTrue(douyin._looks_like_login_page("请扫码登录后继续"))
        self.assertFalse(douyin._looks_like_login_page("作品管理"))
        meta = douyin._worklist_page_meta(
            1,
            {"status": 403, "textPrefix": "<html>blocked</html>"},
            {"status_code": 1001, "status_msg": "risk", "has_more": False},
            [],
        )
        self.assertEqual(meta["status"], 403)
        self.assertEqual(meta["textPrefix"], "<html>blocked</html>")
        self.assertEqual(meta["api_error"], {"status_code": 1001, "status_msg": "risk"})
        self.assertTrue(douyin._worklist_likely_login_required("", [
            {"api_error": {"status_code": 8}},
        ]))
        self.assertFalse(douyin._worklist_likely_login_required("", [meta]))

    def test_douyin_fan_trend_rows(self):
        captured = "2026-06-02T12:00:00+08:00"
        payload = {
            "data": {
                "new_fans": {
                    "option_list": [
                        {"date": "2026-05-31", "count": "1,234", "last_day_incr_rate": "+10%"},
                        {"date": "2026-06-01", "count": "2883"},
                    ],
                },
                "fans": {
                    "option_list": [
                        {"date": "2026-05-31", "count": "83,000"},
                        {"date": "2026-06-01", "count": "85,883"},
                    ],
                },
                "profile": {
                    "option_list": [
                        {"date": "2026-05-31", "count": "1,111"},
                        {"date": "2026-06-01", "count": "2,222"},
                    ],
                },
                "account_search": {
                    "option_list": [
                        {"date": "2026-05-31", "count": "33"},
                        {"date": "2026-06-01", "count": "44"},
                    ],
                },
                "post_search": {
                    "option_list": [
                        {"date": "2026-05-31", "count": "55"},
                        {"date": "2026-06-01", "count": "66"},
                    ],
                },
                "cancel_fans": {
                    "option_list": [
                        {"date": "2026-05-31", "count": "12"},
                        {"date": "2026-06-01", "count": "95"},
                    ],
                },
            },
        }
        rows = douyin._douyin_fan_trend_rows(payload, "xgame", captured)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["schema_version"], schema.SCHEMA_VERSION)
        self.assertEqual(rows[0]["platform"], "douyin")
        self.assertEqual(rows[0]["fan_inc"], 1234)
        self.assertEqual(rows[0]["follower_plays"], 83000)
        self.assertEqual(rows[0]["profile_views"], 1111)
        self.assertEqual(rows[0]["account_searches"], 33)
        self.assertEqual(rows[0]["post_searches"], 55)
        self.assertEqual(rows[0]["unfollow_count"], 12)
        self.assertEqual(rows[0]["fan_inc_last_day_incr_rate"], "+10%")
        self.assertEqual(rows[1]["fan_inc"], 2883)

    def test_douyin_fan_trend_days_type(self):
        self.assertEqual(douyin._overview_days_type(7), 1)
        self.assertEqual(douyin._overview_days_type(15), 2)
        self.assertEqual(douyin._overview_days_type(30), 3)
        with self.assertRaises(CollectorError):
            douyin._overview_days_type(90)

    def test_comments_no_api_diagnostics(self):
        diag = douyin._comments_no_api_diagnostics("扫码登录", 0)
        self.assertFalse(diag["comment_api_seen"])
        self.assertTrue(diag["landing_on_login_page"])
        self.assertEqual(diag["api_pages_intercepted"], 0)
        self.assertTrue(douyin._comments_no_api_diagnostics("作品详情", 2)["comment_api_seen"])


class CanonicalSchema(unittest.TestCase):
    def test_video_row_shape_and_null_metrics_dropped(self):
        row = schema.video_row(
            platform="bilibili", account="xgame", content_id="BV1xx", title="t",
            published_at="2026-05-29T17:45:00+08:00", captured_at="2026-06-01T00:00:00+08:00",
            source_url="u", metrics={"plays": 10, "likes": 2, "shares": None, "fans": 0})
        self.assertEqual(row["schema_version"], schema.SCHEMA_VERSION)
        self.assertEqual(row["content_id"], "BV1xx")
        self.assertEqual(row["metrics"], {"plays": 10, "likes": 2, "fans": 0})  # None dropped, 0 kept
        self.assertNotIn("shares", row["metrics"])
        for k in ("platform", "account", "title", "published_at", "captured_at", "source_url"):
            self.assertIn(k, row)

    def test_video_row_null_content_id(self):
        row = schema.video_row(platform="douyin", account="xgame", content_id=None, title="t",
                               published_at=None, captured_at="c", metrics={"fans": 5})
        self.assertIsNone(row["content_id"])

    def test_video_row_stringifies_numeric_content_id(self):
        self.assertEqual(schema.video_row(platform="douyin", account="x", content_id=7645247646638066994,
                                          title=None, published_at=None, captured_at="c",
                                          metrics={})["content_id"], "7645247646638066994")

    def test_fan_trend_row_shape(self):
        row = schema.fan_trend_row(platform="bilibili", account="xgame", date="2026-05-30",
                                   fan_inc=123, captured_at="c")
        self.assertEqual((row["platform"], row["date"], row["fan_inc"]), ("bilibili", "2026-05-30", 123))
        self.assertEqual(row["schema_version"], schema.SCHEMA_VERSION)


class _SeqClient:
    """Minimal stand-in for httpx.Client: returns/raises a scripted sequence of steps."""

    def __init__(self, steps):
        self._steps = list(steps)
        self.calls = 0

    def get(self, url, params=None):
        self.calls += 1
        step = self._steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.url = "https://api.bilibili.com/test"

    def json(self):
        return self._payload


class RetryBehavior(unittest.TestCase):
    def test_archive_compare_indexes_coin_stats_by_bvid(self):
        client = _SeqClient([_Resp(200, {"code": 0, "data": {"list": [
            {"bvid": "BV1", "stat": {"coin": 12, "fav": 3, "share": 4, "dm": 5}},
            {"bvid": "", "stat": {"coin": 99}},
        ]}})])
        by_bvid = bilibili._archive_compare_by_bvid(client)
        self.assertEqual(client.calls, 1)
        self.assertEqual(by_bvid["BV1"]["stat"]["coin"], 12)
        self.assertNotIn("", by_bvid)

    def test_bilibili_video_row_extra_enriches_metadata(self):
        client = _SeqClient([
            _Resp(200, {"code": 0, "data": {
                "duration": 610,
                "pic": "https://img.example/view.jpg",
                "tname": "科技",
                "tid": 188,
                "copyright": 1,
            }}),
            _Resp(200, {"code": 0, "data": [
                {"tag_name": "AI"},
                {"name": "教程"},
            ]}),
        ])
        extra = bilibili._bilibili_video_row_extra(client, {
            "bvid": "BV1",
            "duration": "10:05",
            "pic": "https://img.example/archive.jpg",
            "typename": "知识",
            "typeid": 36,
            "state": "published",
        })
        self.assertEqual(client.calls, 2)
        self.assertEqual(extra["duration_s"], 605)
        self.assertEqual(extra["cover_url"], "https://img.example/archive.jpg")
        self.assertEqual(extra["category"], "知识")
        self.assertEqual(extra["category_id"], 36)
        self.assertEqual(extra["tags"], ["AI", "教程"])
        self.assertEqual(extra["status"], "published")
        self.assertEqual(extra["copyright"], 1)
        self.assertTrue(extra["is_original"])

    def test_retries_transient_then_succeeds(self):
        client = _SeqClient([httpx.ConnectError("boom"), _Resp(503),
                             _Resp(200, {"code": 0, "data": {"ok": 1}})])
        obj = bilibili._get_json(client, "u", retries=3, backoff_s=0)
        self.assertEqual(obj["data"]["ok"], 1)
        self.assertEqual(client.calls, 3)

    def test_risk_code_fails_fast_without_retry(self):
        client = _SeqClient([_Resp(200, {"code": -412, "message": "blocked"})])
        with self.assertRaises(CollectorError):
            bilibili._get_json(client, "u", retries=3, backoff_s=0)
        self.assertEqual(client.calls, 1)  # never hammered a risk-control code

    def test_other_4xx_not_retried(self):
        client = _SeqClient([_Resp(404)])
        with self.assertRaises(CollectorError):
            bilibili._get_json(client, "u", retries=3, backoff_s=0)
        self.assertEqual(client.calls, 1)

    def test_exhausts_retries_on_persistent_5xx(self):
        client = _SeqClient([_Resp(500), _Resp(500), _Resp(500), _Resp(500)])
        with self.assertRaises(CollectorError):
            bilibili._get_json(client, "u", retries=3, backoff_s=0)
        self.assertEqual(client.calls, 4)  # 1 initial + 3 retries


@unittest.skipUnless(jsonschema is not None, "jsonschema not installed (pip install -e '.[dev]')")
class SchemaConformance(unittest.TestCase):
    """Emitted rows must validate against schemas/collector-output.schema.json."""

    def _check(self, instance, def_name):
        sub = {"$ref": f"#/$defs/{def_name}", "$defs": _SCHEMA["$defs"]}
        jsonschema.Draft202012Validator(sub).validate(instance)

    def test_video_row_conforms(self):
        self._check(schema.video_row(
            platform="bilibili", account="x", content_id="BV1", title="t",
            published_at="2026-05-29T17:45:00+08:00", captured_at="2026-06-01T00:00:00+08:00",
            source_url="u", metrics={"plays": 10, "likes": 2, "fans": 0}), "video_row")

    def test_video_row_metadata_conforms(self):
        row = schema.video_row(
            platform="douyin", account="x", content_id="123", title="t",
            published_at="2026-05-29T17:45:00+08:00", captured_at="2026-06-01T00:00:00+08:00",
            source_url="u", metrics={"plays": 10, "shares": 2})
        row.update({
            "join_key": "title-published:1234567890abcdef",
            "duration_s": 180.123,
            "cover_url": "https://img.example/cover.jpg",
            "category": "知识",
            "category_id": 36,
            "tags": ["AI", "教程"],
            "work_type": 4,
            "status": 2,
            "visibility": "public",
            "audit_status": "pass",
            "copyright": 1,
            "is_original": True,
            "platform_fields": {"forward": 0},
        })
        self._check(row, "video_row")

    def test_video_row_null_content_id_conforms(self):
        self._check(schema.video_row(
            platform="douyin", account="x", content_id=None, title=None,
            published_at=None, captured_at="c", metrics={"fans": 5}), "video_row")

    def test_fan_trend_row_conforms(self):
        self._check(schema.fan_trend_row(
            platform="bilibili", account="x", date="2026-05-30", fan_inc=12, captured_at="c"),
            "fan_trend_row")


if __name__ == "__main__":
    unittest.main()
