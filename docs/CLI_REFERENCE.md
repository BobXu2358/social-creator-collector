# CLI and Output Reference

This is the user-facing reference for Social Creator Collector's CLI,
authentication requirements, output files, and command envelopes. The argparse
tree in `collector/cli.py`, command implementations, and tests remain the final
authority when behavior changes.

Use either equivalent entry point:

```bash
python -m collector <group> <action> --account <account> [options]
collector <group> <action> --account <account> [options]
```

All dates and capture timestamps use `Asia/Shanghai` unless a platform-sourced
field explicitly says otherwise.

## Global options and path contract

| Option | Required/default | Meaning |
|---|---|---|
| `--version` | off | Print the collector version and exit. |
| `--workspace PATH` | current directory | Workspace containing `social/`. |
| `--account NAME` | required | Local namespace, not a platform account ID. |
| `--profile NAME` | `default` | Credential/storage-state profile basename. |
| `--debug` | off | Print the full traceback instead of only `ERROR: …`. |

Default credential and state paths are derived from `--workspace`, `--account`,
and `--profile`:

```text
social/_secrets/<account>/bilibili/<profile>.credentials.json
social/_secrets/<account>/douyin/<profile>.cookies.json
social/_secrets/<account>/douyin/<profile>.storage_state.json
social/<account>/bilibili/raw/
social/<account>/bilibili/processed/
social/<account>/douyin/raw/
social/<account>/douyin/processed/
```

Override options (`--credential`, `--cookies`, `--storage-state`) default to the
paths above when omitted. `--chromium` defaults to Playwright's bundled Chromium;
supported overrides are a binary path, the `$SCC_CHROMIUM` environment variable,
or `$SCC_CHROMIUM_CHANNEL=chrome` to use installed Chrome.

Every command prints one indented multi-line JSON object to stdout. Collection
commands also write the files listed in their command table. `bilibili comments`
and `douyin comments` write JSON only; Markdown behavior for other commands is
shown in the table.

## Bilibili commands

| Command | Options and defaults |
|---|---|
| `init` | global options only; creates both platforms' secret/output directories and examples |
| `bilibili login` | `--credential PATH`; `--timeout 180` in `1..3600`; `--chromium PATH` |
| `bilibili probe` | `--credential PATH` |
| `bilibili summary` | `--credential PATH`; `--days 30` in `1..3650` |
| `bilibili video-detail` | required `--bvid BVID`; `--credential PATH`; `--no-peers` |
| `bilibili dynamics` | `--credential PATH`; `--days 30` in `1..3650`; `--max-pages 10` in `1..100`; `--host-mid MID` |
| `bilibili fan-source` | `--credential PATH` |
| `bilibili comments` | exactly one of `--bvid BVID` / `--aid AID`; `--credential PATH`; `--sessdata VALUE`; `--max-pages 10` in `1..500`; `--delay-ms 600` in `0..60000` |
| `bilibili danmaku` | exactly one of `--bvid BVID` / `--cid CID`; `--bucket-s 10` in `1..3600`; `--peak-n 5` in `1..100`; `--peak-method topn|zscore`; `--no-filter` |

Authentication:

- QR `login` uses a headed Playwright browser and requires Chromium plus a desktop session.
- `probe`, `summary`, `video-detail`, `dynamics`, `fan-source`, and `comments` collect over HTTP using credentials.
- `danmaku` is public and needs no login cookie.
- A browser-free Bilibili setup is possible only when the credentials file is supplied manually rather than created by QR login.

## Douyin commands

| Command | Options and defaults |
|---|---|
| `douyin login` | `--storage-state PATH`; `--timeout 180` in `1..3600`; `--chromium PATH` |
| `douyin check-cookies` | `--cookies PATH` |
| `douyin import-cookies` | `--cookies PATH`; `--nickname TEXT`; `--douyin-id ID`; `--chromium PATH` |
| `douyin worklist` | `--storage-state PATH`; `--days 30` in `0..3650`, where `0` means no cutoff; `--max-pages 20` in `1..500`; `--chromium PATH` |
| `douyin item-analysis` | `--storage-state PATH`; `--days 30` in `1..3650`; `--chromium PATH` |
| `douyin video-detail` | required `--aweme-id ID`; `--storage-state PATH`; `--chromium PATH` |
| `douyin fan-trend` | `--storage-state PATH`; `--days 30`, choices `7`, `15`, `30`; `--chromium PATH` |
| `douyin fan-growth` | `--storage-state PATH`; `--max-scroll 40` in `1..500`; `--chromium PATH` |
| `douyin comments` | required `--aweme-id ID`; `--storage-state PATH`; `--max-pages 20` in `1..500`; `--chromium PATH` |

Douyin login and collection use Playwright. `check-cookies` only validates the
Cookie-Editor JSON file shape; `import-cookies` converts and verifies it through
Playwright.

## Output filenames

| Command | Raw JSON | Processed Markdown |
|---|---|---|
| Bilibili summary | `bilibili-creator-summary-{days}d-{stamp}.json` | same stem `.md` |
| Bilibili video-detail | `bilibili-video-detail-{bvid}-{stamp}.json` | same stem `.md` |
| Bilibili fan-source | `bilibili-fan-source-{stamp}.json` | same stem `.md` |
| Bilibili dynamics | `bilibili-dynamics-{days}d-{stamp}.json` | same stem `.md` |
| Bilibili comments | `bilibili-comments-{bvid}-{stamp}.json` | none |
| Bilibili danmaku | `bilibili-danmaku-{cid}-{stamp}.json` | same stem `.md` |
| Douyin fan-trend | `douyin-fan-trend-{days}d-{stamp}.json` | same stem `.md` |
| Douyin worklist | `douyin-worklist-{days\|all}d-{stamp}.json` | same stem `.md` |
| Douyin item-analysis | `douyin-item-analysis-{days}d-{stamp}.json` | same stem `.md` |
| Douyin video-detail | `douyin-video-detail-{aweme_id}-{stamp}.json` | same stem `.md` |
| Douyin fan-growth | `douyin-fan-growth-{stamp}.json` | same stem `.md` |
| Douyin comments | `douyin-comments-{aweme_id}-{stamp}.json` | none |

`bilibili danmaku --bvid` resolves every part's `cid`; for multipart videos it
emits one JSON/Markdown pair per `cid`.

## Stdout results

The JSON object printed to stdout is a command-specific result summary. It is not
the same object as the persisted raw file.

| Command | Stdout keys | Notes |
|---|---|---|
| `init` | `ok`, `account`, `created` | `created` lists created secret/output/example paths. |
| `bilibili login` | `ok`, `credential`, `mid`, `method` | `method` = `qr-login`. |
| `bilibili probe` | `ok`, `isLogin`, `mid`, `uname`, `level` | |
| `bilibili summary` | `ok`, `json`, `markdown`, `account_fan_total`, `fan_inc_total`, `videos` | Paths to the persisted files. |
| `bilibili video-detail` | `ok`, `json`, `markdown`, `bvid`, `avg_watch_duration_s`, `avg_completion_pct`, `bounce_rate_3s_pct`, `ctr_vs_peer_median`, `retention_points` | |
| `bilibili dynamics` | `ok`, `json`, `markdown`, `host_mid`, `count`, `lottery_count` | |
| `bilibili fan-source` | `ok`, `json`, `markdown`, `source_total`, `sources` | |
| `bilibili comments` | `ok`, `json`, `bvid`, `aid`, `comments` | No `markdown`. |
| `bilibili danmaku` | `ok`, `parts` | Each part has `cid`, `count`, `json`, `markdown`. |
| `douyin login` | `ok`, `storage_state`, `method` | `method` = `qr-login`. |
| `douyin check-cookies` | `ok`, `path`, `cookie_count`, `douyin_domain_cookie_count`, `other_domain_cookie_count`, `important_names_present` | |
| `douyin import-cookies` | `ok`, `storage_state`, `verification` | `verification` contains `ok`, `login_page_seen`, `creator_marker_seen`, `api_ok`, `api_status`, `api_error`, `nickname_seen`, `douyin_id_seen`. |
| `douyin fan-trend` | `ok`, `json`, `markdown`, `account_fan_total`, `fan_inc_total`, `rows` | |
| `douyin worklist` | `ok`, `json`, `markdown`, `account_fan_total`, `items`, `selected` | |
| `douyin item-analysis` | `ok`, `json`, `markdown`, `items` | |
| `douyin video-detail` | `ok`, `json`, `markdown`, `aweme_id`, `completion_rate_pct`, `avg_watch_duration_s`, `traffic_sources`; `partial`, `warning` when item metrics are unavailable but identified, request-bound detail data exists | `ok=true, partial=true` does not guarantee every video metric is present; unavailable metrics are omitted. A response without work identity, or with unbound/mismatched detail, still fails. |
| `douyin fan-growth` | `ok`, `json`, `markdown`, `rows` | |
| `douyin comments` | `ok`, `json`, `aweme_id`, `comments`; `warning` when no API responses | No `markdown`. |

## Persisted raw-file envelopes

Collection commands write a raw JSON file whose envelope keys differ from the
stdout summary. `schema_version: "2.0"` is a version marker; it does not mean the
envelope contains only canonical rows. `bilibili fan-source` and `bilibili
dynamics` carry `schema_version` but use command-specific row structures.

| Command | Raw-file envelope keys | Notes |
|---|---|---|
| Bilibili summary | `schema_version`, `account`, `platform`, `source`, `captured_at`, `range`, `video_range`, `field_notes`, `account_fan_total`, `fan_inc_total`, `fan_trend`, `videos` | `fan_trend` rows are `fan_trend_row`; `videos` rows are `video_row`. |
| Bilibili video-detail | `schema_version`, `account`, `platform`, `source`, `captured_at`, `bvid`, `cid`, `field_notes`, `video` | `video` is a `video_row`. |
| Bilibili fan-source | `schema_version`, `account`, `platform`, `source`, `captured_at`, `source_total`, `sources` | Command-specific `sources` rows. |
| Bilibili dynamics | `schema_version`, `account`, `platform`, `source`, `captured_at`, `host_mid`, `window_days`, `count`, `by_type`, `lottery_count`, `dynamics` | Command-specific `dynamics` rows. |
| Bilibili comments | `account`, `platform`, `bvid`, `aid`, `collected_at`, `comment_count`, `comments` | No `schema_version`. |
| Bilibili danmaku | `cid`, `title`, `count`, `fetched_at`, `danmaku`, `analysis` | No `schema_version`; uses `fetched_at`. |
| Douyin fan-trend | `schema_version`, `account`, `platform`, `source`, `captured_at`, `range`, `metric_labels`, `field_notes`, `account_fan_total`, `fan_inc_total`, `fan_trend` | `fan_trend` rows are `fan_trend_row`. |
| Douyin worklist | `schema_version`, `account`, `platform`, `source`, `captured_at`, `range`, `account_fan_total`, `field_notes`, `page_count`, `item_count`, `items`, `selected_items`, `pages`; `warning`, `diagnostics` when no items | `items`/`selected_items` rows are `video_row`; `diagnostics` includes `landing_on_login_page`, `likely_login_required`, `pages`. |
| Douyin item-analysis | `schema_version`, `account`, `platform`, `source`, `captured_at`, `range`, `field_notes`, `account_overview`, `item_count`, `items`; `warning` when no items | `items` rows are `video_row`. |
| Douyin video-detail | `schema_version`, `account`, `platform`, `source`, `captured_at`, `aweme_id`, `endpoints_seen`, `field_notes`, `video`; `partial`, `warning`, `diagnostics` when item metrics are unavailable but identified, request-bound detail data exists | `video` is a `video_row`; partial diagnostics use `reason=item_compare_metrics_unavailable`, record the non-secret `identity_source`, and list preserved `available_data`. No signed URL or full query string is stored. |
| Douyin fan-growth | `schema_version`, `account`, `platform`, `metric`, `source`, `captured_at`, `scroll_rounds`, `row_count`, `rows`, `field_notes`, `note` | `rows` are `video_row` with null `content_id`. |
| Douyin comments | `account`, `platform`, `aweme_id`, `collected_at`, `comment_count`, `api_pages_intercepted`, `comments`; `warning`, `diagnostics` when no API responses | No `schema_version`; `diagnostics` includes `api_pages_intercepted`, `comment_api_seen`, `landing_on_login_page`. |

Canonical row shapes are defined in `schemas/collector-output.schema.json`:

| Row type | Definition |
|---|---|
| `video_row` | Per-work metrics, metadata, and optional `detail` block. Used by summary, detail, worklist, item-analysis, and fan-growth commands. |
| `fan_trend_row` | Daily net fan change. Used by Bilibili summary and Douyin fan-trend. |

Comments, danmaku, dynamics rows, and fan-source rows use command-specific
structures that are not covered by the JSON Schema `$defs`.

## Date windows and account-level semantics

Bilibili `summary` uses two independent windows:

```text
range.end         = latest date present in the fan-trend API response
range.start       = range.end - (days - 1)
video_range.end   = collection date in Asia/Shanghai
video_range.start = video_range.end - (days - 1)
```

- `account_fan_total` is the current account follower count.
- `fan_inc_total` is the sum of daily net fan changes in the selected trend window.

## Cross-platform comparison

| Concept | Bilibili | Douyin |
|---|---|---|
| `metrics.fans` | cumulative attributed fans | DOM 粉丝增量 within the publish-time window |
| Completion | `avg_completion_pct` is mean watched fraction | `completion_rate_pct` is the true reached-the-end rate |
| Retention | per-second still-watching curve | drag-back/drag-forward distributions |
| Traffic/source | terminal distribution, not recommend/search traffic | recommendation/follow/search/profile traffic source |
| CTR | relative peer signals only; no reliable absolute CTR or impressions | absolute `cover_click_rate_pct` when available |
| Early bounce | Bilibili 3-second rate | Douyin 2-second rate |

`_pct` metrics are normalized percentages, while Bilibili summary's
`full_play_ratio` is a platform raw basis-point value and must not be treated as
an already-normalized percent.
