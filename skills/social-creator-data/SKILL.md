---
name: social-creator-data
description: Collect read-only Bilibili and Douyin creator-center data — fan trends, per-video metrics, work lists, and Douyin per-video fan growth (粉丝增量). Use when a user asks to set up, onboard, verify, or run B站/哔哩哔哩 or 抖音 creator backend data collection with Cookie-Editor exports, SESSDATA/bili_jct/buvid3, Douyin creator-center cookies, 涨粉/投币 trends, 稿件数据, or cross-platform creator analytics.
---

# Social Creator Data

Set up and operate the bundled read-only collector for Bilibili & Douyin creator data.
The collector is a single CLI: `python -m collector <group> <action> --account <account>`.

## Safety

- Follow the repository-wide safety, credential, and discovery rules in
  [`AGENTS.md`](../../AGENTS.md).
- Never read, index, print, or return raw values from `_secrets/`.
- Use [`docs/CLI_REFERENCE.md`](../../docs/CLI_REFERENCE.md) for complete options,
  defaults, authentication requirements, and output contracts.

## Collecting

```bash
# B站 collection is HTTP-based; QR login itself uses a headed browser
python -m collector bilibili probe   --account <account>
python -m collector bilibili summary --account <account> --days 30

# 抖音 — Playwright (after `douyin login`, or import-cookies fallback)
python -m collector douyin worklist   --account <account> --days 30
python -m collector douyin fan-growth --account <account>   # 粉丝增量, DOM-only
```

`bilibili summary` returns daily fan increments plus per-video play/fans/**coin**/reply/likes
— that's the涨粉 + 投币 data a monthly performance workflow needs.

Its date fields are intentionally different: `range` is the fan-trend API window ending
on the latest date that API returned, while `video_range` is the video publish window
ending on the collection date in `Asia/Shanghai`. `fan_inc_total` sums the daily rows in
`range`.

If `bilibili summary` is slow or fails, use its bounded timing evidence before changing
the caller:

- API acquisition has a 90-second shared budget and a 20-second per-request cap;
- successful raw JSON records `diagnostics.request_timing.stages` by sanitized endpoint path;
- failures include elapsed time and `stage_timings`; add `--debug` only when the traceback
  is needed;
- retain a caller-side process timeout (120 seconds is the intended margin) and identify
  the slow endpoint before increasing it to 240 seconds.

## Douyin collaborative works

`douyin worklist` may attach collaboration metadata to a canonical row:

```json
{
  "platform_fields": {
    "is_collaboration": true,
    "creator_role": "collaborator"
  }
}
```

Use it conservatively:

- only `is_collaboration is true` confirms a collaborative work; a missing field is unknown;
- `creator_role` is `primary`, `collaborator`, or `unknown` for the current account;
- retain a collaborator's basic work row, but run `video-detail` under the primary account
  when collaborator-side detail is unavailable;
- never copy account-context-sensitive analytics between accounts.

The collector compares creator IDs in memory and does not emit IDs, collaborator names,
contribution roles, or a generic co-creator count.

## Douyin per-video fan growth (粉丝增量)

There is **no API** for per-video fan growth; it lives only in the 投稿列表 table DOM of
`creator.douyin.com/creator-micro/data-center/content`. The `douyin fan-growth` command:
1. opens that page with the imported storage state,
2. clicks the 投稿列表 tab,
3. **scrolls the window until the rendered row count stops growing** (the table lazy-loads on
   scroll — there is no pager), then extracts the table and locates the 粉丝增量 column
   **by header text** (not a fixed index),
4. **fails loud** if the 粉丝增量 header is gone — Douyin redesigned the table and
   `collector/douyin.py` (`_EXTRACT_TABLE_JS` / `fan_growth`) needs re-inspection.

**Scope:** the 投稿列表 is bounded by its 发布时间 (publish-time) filter — by default it returns
roughly the last ~3 months of works (verified live: 13 rows back to ~90 days), which covers a
monthly cadence comfortably. Pulling *older* history means widening that date picker, which this
command does **not** automate. `--max-scroll` caps scroll rounds (default 40).

If it errors with "column not found" or "投稿列表 tab not found", open the page in a headed
browser, inspect the new structure, and update that one function.

## Known pitfalls

- B站 direct public APIs can hit `412`/`-799`/`-352`/`-403`; the collector uses creator-center
  paths with cookies and raises (doesn't hammer) on those codes.
- Douyin QR is risk-controlled: `douyin login` runs a **headed local** browser (works where
  headless/remote is rejected). If even the automated browser gets flagged, fall back to
  Cookie-Editor + `import-cookies`.
- Cookie expiry is the most common failure. `probe` / `import-cookies` fail loud; if a collect
  command returns empty or a login warning, re-export before debugging.
- Don't store transient STS/upload/IM tokens seen in creator-center network logs.

For comment and danmaku feedback analysis, use the `feedback-analytics` skill.
