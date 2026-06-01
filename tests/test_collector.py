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
        self.assertEqual(c["sameSite"], "Lax")
        self.assertEqual(c["path"], "/")
        self.assertNotIn("extra", c)
        self.assertEqual(douyin._normalize_cookie({"name": "x", "value": "y", "sameSite": "none"})["sameSite"], "None")

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

    def test_fetch_danmaku_unescapes_xml_entities(self):
        import zlib as _zlib
        xml = '<i><d p="1.0,1,25,16777215,0,0">A &amp; B &lt;tag&gt; &#39;q&#39;</d></i>'
        co = _zlib.compressobj(9, _zlib.DEFLATED, -_zlib.MAX_WBITS)
        raw = co.compress(xml.encode("utf-8")) + co.flush()

        class _Resp:
            content = raw

            def raise_for_status(self):
                pass

        class _Client:
            def get(self, url, params=None):
                return _Resp()

        dms = bilibili.fetch_danmaku(_Client(), cid=1)
        self.assertEqual(len(dms), 1)
        self.assertEqual(dms[0]["content"], "A & B <tag> 'q'")

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

    def test_normalize_aweme_camelcase_and_snakecase(self):
        camel = douyin._normalize_aweme({
            "AwemeId": 123, "Desc": "标题", "CreateTime": 1716000000,
            "Statistics": {"PlayCnt": 1000, "DiggCnt": 50, "CommentCnt": 7,
                           "ShareCnt": 2, "CollectCnt": 3},
        })
        self.assertEqual(camel["aweme_id"], 123)
        self.assertEqual(camel["title"], "标题")
        self.assertEqual((camel["play"], camel["like"], camel["comment"],
                          camel["share"], camel["collect"]), (1000, 50, 7, 2, 3))
        self.assertEqual(camel["url"], "https://www.douyin.com/video/123")
        snake = douyin._normalize_aweme({"aweme_id": "456", "desc": "t2",
                                         "create_time": 1716000000, "play_count": 9, "digg_count": 1})
        self.assertEqual((snake["aweme_id"], snake["play"], snake["like"]), ("456", 9, 1))


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
