---
name: social-creator-data
description: Collect read-only Bilibili and Douyin creator-center data — fan trends, per-video metrics, work lists, and Douyin per-video fan growth (粉丝增量). Use when a user asks to set up, onboard, verify, or run B站/哔哩哔哩 or 抖音 creator backend data collection with Cookie-Editor exports, SESSDATA/bili_jct/buvid3, Douyin creator-center cookies, 涨粉/投币 trends, 稿件数据, or cross-platform creator analytics.
---

# Social Creator Data

Set up and operate the bundled read-only collector for Bilibili & Douyin creator data.
The collector is a single CLI: `python -m collector <group> <action> --account <account>`.

## Safety

- Read-only only: no posting, editing, deleting, commenting, following, or account settings.
- Prefer local-file cookie onboarding; never ask the user to paste cookies into chat (chat-paste is a fallback only).
- Never print cookie values, tokens, or storage state.
- One namespace per account. Never index `_secrets/` into memory.

## Setup

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium    # Douyin only
```

The Douyin commands launch Playwright's bundled Chromium by default — no path to
configure. Override with `--chromium <path>` / `$SCC_CHROMIUM` if needed.

## Onboarding

Preferred — QR scan login. A headed browser opens; the human scans the platform's own
QR with their phone; the session is saved automatically.

```bash
python -m collector init            --account <account>
python -m collector bilibili login  --account <account>   # → credential file
python -m collector douyin   login  --account <account>   # → storage state
```

`login` needs a desktop session (headed browser). When cookies expire, re-run it.

Fallback (QR rejected, or headless host) — Cookie-Editor export → JSON:
- B站: `social/_secrets/<account>/bilibili/default.credentials.json` — `{"SESSDATA":"...","bili_jct":"...","buvid3":"..."}`
- 抖音: `social/_secrets/<account>/douyin/default.cookies.json` (then `douyin import-cookies`)

## Collecting

```bash
# B站 — pure HTTP, no browser
python -m collector bilibili probe   --account <account>
python -m collector bilibili summary --account <account> --days 30

# 抖音 — Playwright (after `douyin login`, or import-cookies fallback)
python -m collector douyin worklist   --account <account> --days 30
python -m collector douyin fan-growth --account <account>   # 粉丝增量, DOM-only
```

`bilibili summary` returns daily fan increments plus per-video play/fans/**coin**/reply/likes
— that's the涨粉 + 投币 data a monthly performance workflow needs.

## Douyin per-video fan growth (粉丝增量)

There is **no API** for per-video fan growth; it lives only in the 投稿列表 table DOM of
`creator.douyin.com/creator-micro/data-center/content`. The `douyin fan-growth` command:
1. opens that page with the imported storage state,
2. clicks the 投稿列表 tab,
3. extracts the table and locates the 粉丝增量 column **by header text** (not a fixed index),
4. **fails loud** if the 粉丝增量 header is gone — meaning Douyin redesigned the table and
   `collector/douyin.py` (`_EXTRACT_TABLE_JS` / `fan_growth`) needs re-inspection.

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
