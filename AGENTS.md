# Social Creator Collector — Agent Instructions

A read-only toolkit for Bilibili & Douyin creator data. One CLI, two platforms.
You set it up and run it safely — never paste secrets into chat, never print cookie values.

## Safety rules (read first)

- **Read-only only**: no posting, editing, deleting, commenting, DM, following, account settings, or login changes.
- **Never print cookies/tokens/storage state.** Length/count is fine; raw values are not.
- **One namespace per account**: everything lives under `social/<account>/` and `social/_secrets/<account>/`.
- **`_secrets/` never enters memory, indexing, or any shared output.** It is gitignored — keep it that way.

## Read-only discovery fallback

Use the supported `collector` commands first. If they do not expose enough data for
the user's analysis, you may do **read-only discovery** as a temporary fallback:

- Stay on official creator-center domains only:
  - Bilibili: `member.bilibili.com`, `api.bilibili.com`
  - Douyin: `creator.douyin.com`, `www.douyin.com`
- Only inspect pages and GET/fetch already-used creator data APIs. Do not post, edit,
  delete, publish, comment, DM, follow, change settings, export private account lists,
  or trigger any mutation.
- Do not modify this repo, the installed package, or core collector code during a
  user task. Discovery results are for the current analysis only.
- Do not print or save raw cookies, tokens, `storage_state`, `msToken`, `a_bogus`,
  auth headers, full signed query strings, request bodies, private user lists, or
  raw comment/message dumps.
- Keep discovery bounded: a small number of pages/endpoints, short timeouts, and no
  broad crawling. Prefer field names, counts, and schema summaries over raw payloads.
- Clearly label any discovered field whose meaning is an inference, not a documented
  fact. If a field is ambiguous, say so instead of building a confident conclusion.

After completing the user's task, if discovery found data that looks broadly useful
or missing from core, open an upstream issue instead of patching locally. Include:

- the business question that needed the data;
- platform, account namespace (not platform account id unless needed), and supported
  commands that were insufficient;
- sanitized endpoint paths (drop tokens/signatures/query secrets);
- a redacted field-shape summary and example counts, not raw sensitive values;
- inferred field meanings and uncertainty;
- whether it reproduced more than once;
- the exact collector version/commit.

Use the repository's "Discovery finding" issue template when available. The maintainer
decides whether the data should become a supported core command.

## Setup

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e .                         # or: pip install -r requirements.txt
python -m playwright install chromium    # only needed for the Douyin commands
```

`pip install -e .` also puts a `collector` command on PATH (identical to
`python -m collector`). Examples below use `python -m collector` so they work
without installing.

### On Windows (PowerShell)

Same package — three Windows-specific gotchas, worth knowing since a coding agent may be
driving this on a colleague's Windows machine:

- **Use a real Python, not the Store stub.** A bare `python` on Windows is usually the
  Microsoft Store alias: it prints nothing and exits non-zero (9009), or pops the Store.
  Install CPython (`winget install Python.Python.3.12`, or python.org) and confirm with
  `py -V` before anything else.
- **venv + install, PowerShell syntax** (the bash `source .../activate` line won't work):
  ```powershell
  py -3 -m venv .venv
  .\.venv\Scripts\Activate.ps1
  pip install -e .
  python -m playwright install chromium   # only for the Douyin commands
  ```
- **`tzdata` is pulled in automatically** on Windows (the package depends on it). Without a tz
  database, `ZoneInfo("Asia/Shanghai")` makes every command fail at import — so don't strip it.

Bilibili is pure HTTP, so a **B站-only** setup needs just Python + `pip install` — no
`playwright install chromium`, no browser. Chromium (and a desktop session for the headed QR
login) is only required for the Douyin commands.

## Staying current

The tool lives upstream — fixes (especially when Douyin changes its DOM) ship there, not in
your installed copy. There is no auto-update; with a pinned version, updating is deliberate.

- Check your version: `collector --version`. Latest = the highest tag at the repo's `/tags`.
- Update: `pip install -U "git+https://github.com/BobXu2358/social-creator-collector@<tag>"`
  — or track `main` instead of a tag if you want fixes the moment they land.
- **Never patch the installed package locally.** Editing the core forks it, and the next update
  silently reverts you. Fixes go upstream as a PR, then everyone re-installs (one fix heals all).

When a command **fails loud** with a "Douyin changed / column not found / layout changed" error,
that's the known-fragile DOM path: first update to the latest release (it may already be fixed);
if it still fails on the latest version, the fix isn't out yet — report it upstream (open an
issue). Don't hack your copy.

Chromium is resolved cross-platform (see `collector/browser.py`): the default is
Playwright's bundled browser — no hardcoded path. Override with `--chromium <path>`,
`$SCC_CHROMIUM`, or `$SCC_CHROMIUM_CHANNEL=chrome` to use installed Chrome.

## Login / cookie onboarding

**Preferred — QR scan login.** Opens a real browser window; the human scans the
platform's own QR with their phone; the session is saved automatically. No
Cookie-Editor, no manual paste.

```bash
python -m collector init            --account <account>   # folders + examples
python -m collector bilibili login  --account <account>   # → credential file
python -m collector douyin   login  --account <account>   # → storage state
```

`login` runs a **headed** browser, so it needs a desktop session (won't work on a
headless server, and can't run unattended in cron). When cookies expire, just
re-run `login`. Douyin's QR is heavily risk-controlled — if the automated browser
gets rejected, fall back to Cookie-Editor below.

**Fallback — Cookie-Editor export.** Use when QR login is rejected, or on a headless
host. Export cookies as **JSON** (not Netscape text):
https://chromewebstore.google.com/detail/cookie-editor/hlkenndednhfkekhgcdicdfddnkalmdm

- *Mode A (local file)* — save the export, then `douyin import-cookies`:
  - B站: `social/_secrets/<account>/bilibili/default.credentials.json` → `{"SESSDATA":"...","bili_jct":"...","buvid3":"..."}`
  - 抖音: `social/_secrets/<account>/douyin/default.cookies.json` (Cookie-Editor JSON array)
- *Mode B (chat paste)* — if the human pastes cookie JSON in chat: keep only the needed
  keys (B站: `SESSDATA`,`bili_jct`,`buvid3`; 抖音: `sessionid`,`sessionid_ss`,`sid_guard`,`uid_tt`,`passport_csrf_token`),
  write to the `_secrets` path, `chmod 600`, and **never echo the values back** — reply only with the verification result.

## Commands

```bash
python -m collector <group> <action> --account <account> [options]
```

| Command | What it does |
|---|---|
| `init --account X` | create folder structure + example credential files |
| `bilibili login --account X` | QR scan login (headed browser) → credential file |
| `bilibili probe --account X` | verify B站 login + identity (fails loud if cookie expired) |
| `bilibili summary --account X --days 30` | fan trend + per-video play/fans/coin/reply/likes |
| `bilibili video-detail --account X --bvid BVxxx` | single-video retention curve + completion + follower/guest split + audience + 封标点击率 relative signals + 3秒跳出率 |
| `bilibili fan-source --account X` | fan source distribution (video/search/space/etc.) |
| `bilibili comments --account X --bvid BVxxx` | collect top-level video comments |
| `bilibili danmaku --account X --bvid BVxxx` | fetch danmaku + density-peak analysis |
| `douyin login --account X` | QR scan login (headed browser) → storage state |
| `douyin check-cookies --account X` | validate a Cookie-Editor export's structure |
| `douyin import-cookies --account X` | cookies → Playwright storage state + verify login |
| `douyin worklist --account X --days 30` | creator-center work list + basic metrics |
| `douyin item-analysis --account X --days 30` | per-work avg watch time + 5s完播率 + 2s跳出率 (作品分析批量) |
| `douyin video-detail --account X --aweme-id ID` | single-video 完播率 + 流量来源 + 进度曲线 + 搜索词 + 观众画像 (分析详情) |
| `douyin fan-trend --account X --days 30` | daily net fans + related overview metrics |
| `douyin fan-growth --account X` | **per-video 粉丝增量** from 投稿列表 DOM |
| `douyin comments --account X --aweme-id ID` | collect video comments |

Every command prints one JSON result line. Outputs land under
`social/<account>/<platform>/raw/*.json` and `processed/*.md`. On an unexpected error
the CLI prints a one-line `ERROR: …` and exits non-zero; pass `--debug` to any command
for the full traceback.

## Output schema

Per-video and daily-fan-trend data use a canonical, versioned row shape
(`collector/schema.py`, documented in `schemas/collector-output.schema.json`), so a
consumer sees the same field names across platforms — `metrics.plays/likes/comments/
shares/collects/coins/fans`, plus `content_id`, `published_at`, `captured_at`. Every
data output carries `schema_version`; it bumps only on breaking changes. Build
downstream tools against this shape, not against one command's incidental JSON.

## What's worth knowing (so you don't relearn the traps)

- **Douyin per-video fan growth has no API** — it only exists in the 投稿列表 table DOM.
  `douyin fan-growth` locates the 粉丝增量 column by header text and fails loud if Douyin
  redesigns the table (rather than silently returning a wrong column).
- **Douyin account-level daily net fans do have a creator-center overview API.**
  `douyin fan-trend --days 30` reads `new_fans.option_list` from that API; this is the
  right input for campaign lift analysis. It also carries related daily overview metrics
  (profile visits, account/work searches, plays, follower plays, likes/comments/shares,
  unfollows). It is not a local snapshot system.
- **Bilibili fan source is a direct creator-center source split.** `bilibili fan-source`
  emits counts for buckets like video/search/space/recommend/live/other; use it as a
  supporting input next to `summary`'s daily fan trend and per-video fan attribution.
- **Per-video detail is its own command on each platform.** `bilibili video-detail --bvid`
  reads the 稿件分析 APIs: a per-second retention curve, average watch duration,
  average completion vs same-tier peers, and the follower-vs-guest play split (plus
  terminal/region/interest breakdowns). On Douyin there are two layers: `douyin
  item-analysis --days` is the **batch** 作品分析 (per-work avg watch / 5s完播率 / 2s跳出率
  across the window), and `douyin video-detail --aweme-id` is the **single-work** 分析详情
  — full **完播率**, the **流量来源** split (推荐/关注/搜索/个人主页/…), drag-back/forward
  progress curves, the **搜索词** that surfaced the work, and an audience portrait. The
  Douyin detail endpoints reject a raw fetch (the page signs them), so video-detail
  navigates work-detail and intercepts the responses — the same pattern as `comments`.
  All rate fields are pre-normalized to percent.
- **Three platform quirks worth knowing.** Bilibili's `avg_completion_pct` is the mean
  watched fraction (avg progress ÷ duration), not the share who reached the end; its
  "播放量来源" is a *terminal* split (手机/PC/电视), not a recommend/search traffic source —
  Bilibili doesn't expose that per video. Douyin's `completion_rate_pct` (from video-detail)
  IS the real 完播率, and Douyin's "retention" is exposed as drag-back/forward distributions
  by playback second, not a plain still-watching curve.
- **Bilibili CTR is relative-only — impressions/clicks don't exist.** The creator center
  has 封标点击率 (cover+title CTR), but it exposes NO impression/click counts anywhere, and
  it deliberately randomizes the absolute CTR fields (every `tm_*` rate in an API response
  is rescaled by one random factor per response; the UI hides the digits behind an
  obfuscated font). `bilibili video-detail` therefore emits only the stable signals in
  `detail.click_through`: CTR vs same-tier peer median (e.g. 0.7×), fan/guest variants,
  fan-vs-guest CTR ratio, percentile vs peers, and a 0-5 star rating — plus the stable
  absolute `metrics.bounce_rate_3s_pct` (3秒跳出率) with its peer split in `detail.bounce_3s`.
  Both cover only the first 14 days after publish (frozen afterwards). Don't try to recover
  absolute CTR/impressions from Bilibili — the data is obfuscated at the source.
- **B站 comments need a login cookie.** The anonymous/`x/v2/reply/wbi/main` endpoints
  return ~3 hot comments or trigger `412` — the collector uses `x/v2/reply/main` with cookie.
- **Cookie expiry is the usual failure.** If a command returns empty or a login warning,
  re-export cookies before debugging code.
- **bvid↔aid**: resolved via the view API, not offline math (which breaks for post-2023 long aids).

For deeper workflows: `skills/social-creator-data` (creator metrics) and
`skills/feedback-analytics` (comments + danmaku analysis).
