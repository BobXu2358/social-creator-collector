# Social Creator Collector — Agent Instructions

A read-only toolkit for Bilibili & Douyin creator data. One CLI, two platforms.
You set it up and run it safely — never paste secrets into chat, never print cookie values.

## Safety rules (read first)

- **Read-only only**: no posting, editing, deleting, commenting, DM, following, account settings, or login changes.
- **Never print cookies/tokens/storage state.** Length/count is fine; raw values are not.
- **One namespace per account**: everything lives under `social/<account>/` and `social/_secrets/<account>/`.
- **`_secrets/` never enters memory, indexing, or any shared output.** It is gitignored — keep it that way.

## Setup

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium    # only needed for the Douyin commands
```

Chromium is resolved cross-platform (see `collector/browser.py`): the default is
Playwright's bundled browser — no hardcoded path. Override with `--chromium <path>`,
`$SCC_CHROMIUM`, or `$SCC_CHROMIUM_CHANNEL=chrome` to use installed Chrome.

## Cookie onboarding

Install Cookie-Editor and have the human export cookies as **JSON** (not Netscape text):
https://chromewebstore.google.com/detail/cookie-editor/hlkenndednhfkekhgcdicdfddnkalmdm

```bash
python -m collector init --account <account>     # creates folders + example files
```

**Mode A — local file (preferred).** Ask the human to save the export at:
- B站: `social/_secrets/<account>/bilibili/default.credentials.json` →
  `{"SESSDATA":"...","bili_jct":"...","buvid3":"..."}`
- 抖音: `social/_secrets/<account>/douyin/default.cookies.json` (Cookie-Editor JSON array)

**Mode B — chat paste (fallback).** If the human pastes cookie JSON in chat:
1. Keep only the needed keys — B站: `SESSDATA`,`bili_jct`,`buvid3`; 抖音: `sessionid`,`sessionid_ss`,`sid_guard`,`uid_tt`,`passport_csrf_token` (+ any `*sessionid*`/`*csrf*`/`*guard*`/`*uid*`).
2. Write the cleaned JSON to the `_secrets` path above; `chmod 600` it.
3. **Never echo the values back.** Reply only with the verification result (mid/nickname — not cookies).

## Commands

```bash
python -m collector <group> <action> --account <account> [options]
```

| Command | What it does |
|---|---|
| `init --account X` | create folder structure + example credential files |
| `bilibili probe --account X` | verify B站 login + identity (fails loud if cookie expired) |
| `bilibili summary --account X --days 30` | fan trend + per-video play/fans/coin/reply/likes |
| `bilibili comments --account X --bvid BVxxx` | collect top-level video comments |
| `bilibili danmaku --account X --bvid BVxxx` | fetch danmaku + density-peak analysis |
| `douyin check-cookies --account X` | validate a Cookie-Editor export's structure |
| `douyin import-cookies --account X` | cookies → Playwright storage state + verify login |
| `douyin worklist --account X --days 30` | creator-center work list + basic metrics |
| `douyin fan-growth --account X` | **per-video 粉丝增量** from 投稿列表 DOM |
| `douyin comments --account X --aweme-id ID` | collect video comments |

Every command prints one JSON result line. Outputs land under
`social/<account>/<platform>/raw/*.json` and `processed/*.md`.

## What's worth knowing (so you don't relearn the traps)

- **Douyin per-video fan growth has no API** — it only exists in the 投稿列表 table DOM.
  `douyin fan-growth` locates the 粉丝增量 column by header text and fails loud if Douyin
  redesigns the table (rather than silently returning a wrong column).
- **B站 comments need a login cookie.** The anonymous/`x/v2/reply/wbi/main` endpoints
  return ~3 hot comments or trigger `412` — the collector uses `x/v2/reply/main` with cookie.
- **Cookie expiry is the usual failure.** If a command returns empty or a login warning,
  re-export cookies before debugging code.
- **bvid↔aid**: resolved via the view API, not offline math (which breaks for post-2023 long aids).

For deeper workflows: `skills/social-creator-data` (creator metrics) and
`skills/feedback-analytics` (comments + danmaku analysis).
