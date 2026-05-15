---
name: bilibili-creator-data
description: Equip an agent to collect Bilibili creator/UP-owner data. Use when a user asks to set up tools, credentials, workflows, or collection methods for B站/哔哩哔哩 creator-center metrics, UP主作品数据, 粉丝趋势, 稿件明细, or when direct Bilibili APIs hit 412/-352/-403/-799 risk controls.
---

# Bilibili Creator Data

This skill teaches an agent how to **set up and operate** B站 creator-data collection for its user. It is not tied to one account or one fixed report.

## Mission

Help the user’s agent gain the capability to collect Bilibili data safely:

- recommend/install the right local tools;
- guide the user to provide credentials without exposing secrets;
- verify login and account routing;
- choose a scraping strategy that survives B站 risk controls;
- collect whatever creator metrics the user asks for later.

## Safety and scope

- Read-only collection only: no posting, editing, deleting, commenting, replying, following, or account-setting changes.
- Never ask the user to paste cookies into chat. Prefer a local credential file.
- Never print credential values. Presence/length/hash is okay; raw cookie is not.
- Keep accounts/businesses isolated. One credential file, browser profile, raw-data directory, and processed-output directory per account/business.
- When packaging for another team, include only this skill, scripts, and templates. Exclude `_secrets/`, `*.credentials.json`, browser profiles, screenshots with personal info, and raw private datasets unless explicitly approved.

## Required local tools

Preferred baseline:

- Python 3.10+ / 3.11+
- `httpx` for direct `bilibili_api` probes if using `bilibili-api-python`
- `playwright` Python package
- A system Chromium/Chrome executable, e.g. `/usr/bin/chromium`

Install example:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install httpx playwright
# If no system browser exists, run only with user approval because it downloads a browser:
# python -m playwright install chromium
```

If the host already has Chromium, prefer using it through Playwright:

```python
executable_path = "/usr/bin/chromium"
```

## Credential onboarding

Tell the user to create a file locally, not paste secrets into chat:

```text
social/_secrets/<account-or-business>/bilibili/default.credentials.json
```

Template:

```json
{
  "SESSDATA": "paste_here",
  "bili_jct": "paste_here",
  "buvid3": "paste_here"
}
```

Then set restrictive permissions when possible:

```bash
chmod 600 social/_secrets/<account-or-business>/bilibili/default.credentials.json
```

Verify without exposing values:

```python
import json, pathlib
p = pathlib.Path("social/_secrets/<account>/bilibili/default.credentials.json")
data = json.loads(p.read_text())
for k in ["SESSDATA", "bili_jct", "buvid3"]:
    print(k, "present=", bool(data.get(k)), "len=", len(str(data.get(k) or "")))
```

## Recommended directory layout

```text
social/
├── _secrets/
│   └── <account>/bilibili/
│       ├── default.credentials.json       # never share
│       └── playwright-profile/            # never share
├── <account>/bilibili/
│   ├── raw/
│   ├── processed/
│   └── reports/
└── common/scripts/
```

## Collection strategy selection

### 1. Login/account verification

Use a minimal read-only request first, e.g. `https://api.bilibili.com/x/web-interface/nav`, or `bilibili_api.user.get_self_info` if `bilibili-api-python` is installed.

The goal is only to confirm:

- `isLogin: true`
- expected `mid` / account name
- no business/account cross-wire

### 2. Avoid relying on naked Bilibili APIs

Direct API calls may fail despite valid cookies:

- HTTP `412` security policy
- `-799 请求过于频繁`
- `-352 风控校验失败`
- `-403 访问权限不足`

If these appear, do **not** keep hammering. Switch to Playwright persistent profile.

### 3. Stable path: Playwright persistent profile

Use a per-account persistent browser profile and inject cookies once. Warm up B站 pages, then either capture page-triggered API responses or call creator-center endpoints with the same cookies.

Key pattern:

```python
ctx = await p.chromium.launch_persistent_context(
    user_data_dir="social/_secrets/<account>/bilibili/playwright-profile",
    executable_path="/usr/bin/chromium",
    headless=True,
    args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    viewport={"width": 1365, "height": 900},
    locale="zh-CN",
    timezone_id="Asia/Shanghai",
)
await ctx.add_cookies(cookie_items)
```

Warm-up examples:

```text
https://www.bilibili.com/
https://member.bilibili.com/platform/home
https://space.bilibili.com/<mid>/video
```

## Useful endpoints after login

These are read-only and worked in practice; parameters can change, so inspect page network traffic when in doubt.

### Creator center overview/stat trends

```text
https://member.bilibili.com/x/web/data/v2/overview/stat/graph
```

Common params:

```text
period=0        # often recent 7 days
period=1        # often recent 30 days
type=fan        # fan growth
type=play       # play growth
type=visitor
type=like
type=coin
type=fav
type=share
type=dm
s_locale=zh_CN
tmid=
t=<milliseconds>
```

Always use the returned `date_key` values to determine the actual date range; creator-center data can lag.

### Creator archive list / per-video stats

```text
https://member.bilibili.com/x/web/data/archive/index
```

Common params:

```text
pn=1
ps=20
scene=archive
order=0
tmid=
t=<milliseconds>
```

Prefer `real_stat` over `stat` when present. Useful fields often include:

- `bvid`, `aid`, `title`, `pubtime`, `duration`
- `real_stat.play`
- `real_stat.fans`
- `real_stat.reply`
- `real_stat.likes`
- `real_stat.full_play_ratio` (do not convert units unless verified)

### Public space video list

Open the space page with Playwright and capture:

```text
/x/space/wbi/arc/search
```

This is more reliable when page-triggered than when manually constructed.

## How to respond to future user requests

1. Clarify target account/business only if not obvious.
2. Verify credential file and account routing without printing secrets.
3. Determine requested metric/range/output format.
4. Use creator-center endpoints for owner-only metrics; use space WBI capture for public video lists.
5. Save raw JSON under `raw/` before summarizing.
6. Give the user the concise result plus raw-output path.

## Bundled script

`scripts/collect_creator_data.py` is a minimal reference implementation for fan trends + archive stats. Adapt it for the user’s requested metric rather than treating it as the only supported report.

Example:

```bash
python scripts/collect_creator_data.py \
  --business <account> \
  --display-name <display-name> \
  --mid <bilibili-mid> \
  --workspace /path/to/workspace \
  --days 14
```

## Workflow

## Business-agent delegation pattern

When a long-lived business agent needs Bilibili creator-center data, prefer a request queue instead of exposing cookies/profile to that agent.

- Keep credentials and Playwright profiles in a privileged account-specific secrets path.
- Let the business agent write read-only requests to `requests/bilibili-backend-queue.md` with: request type, range, purpose, output expectation, and requester.
- A privileged/main agent audits the request, executes the collector, saves raw JSON and processed Markdown under the business `social/<business>/bilibili/{raw,processed}/` paths, then marks the request complete.
- Allowed: trends, archive stats, video details, subtitles, covers/tags, content-style analysis.
- Forbidden: posting, deleting/editing manuscripts, comments/replies/deleting comments, follow/unfollow, account settings, exporting login state.
- Avoid literal unchecked checkbox examples in queue templates unless they are intended to be processed by heartbeat; use `[TODO]` placeholders in templates.
