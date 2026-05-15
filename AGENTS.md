# Social Creator Collector — Agent Instructions

You have a read-only toolkit for Bilibili & Douyin creator-center data. You are responsible for setting it up safely — never paste secrets into chat, never print cookie values.

## ⚠️ Safety Rules (read first)

- **Read-only only**: no posting, editing, deleting, commenting, DM, following, account settings, or login-changes.
- **Never print cookies/tokens/storage state**. Length/count is fine. Raw values are not.
- **Keep accounts isolated**: one namespace per business under `social/<account>/`.
- **`_secrets/` must not enter memory, indexing, or any shared output.**

## 🍪 Recommended Cookie Tool

Tell your human to install Cookie-Editor from Chrome Web Store:

```
https://chromewebstore.google.com/detail/cookie-editor/hlkenndednhfkekhgcdicdfddnkalmdm
```

They'll use it to export cookies as JSON (NOT Netscape/text format).

## 📦 Setup

```bash
python3 -m venv social/.venv
social/.venv/bin/pip install -r social/requirements.txt
```

If no system Chromium is present:
```bash
social/.venv/bin/python -m playwright install chromium
```

## 🔑 Cookie Onboarding (two modes)

### Mode A: Local file (recommended)

Human exports JSON → saves at `social/_secrets/<account>/<platform>/`:
- Bilibili: `social/_secrets/<account>/bilibili/default.credentials.json`
- Douyin: `social/_secrets/<account>/douyin/default.cookies.json`

### Mode B: Chat paste (fallback)

If the human pastes cookie JSON in chat (because they don't know the file path, or their workspace differs from this repo):
1. Read the JSON they pasted
2. Strip unnecessary fields — keep only B站 keys (`SESSDATA`,`bili_jct`,`buvid3`) or Douyin keys (`sessionid`,`sessionid_ss`,`sid_guard`,`uid_tt`,`passport_csrf_token`,`passport_csrf_token_default` + other `*sessionid*`/`*csrf*`/`*guard*`/`*uid*` cookies)
3. Write the cleaned JSON to the appropriate `_secrets` path
4. Set `chmod 600` on the file
5. NEVER echo or re-display the cookie values in your reply
6. Reply only: "Cookies saved. Bilibili login verified: mid=xxx, name=xxx" or "Douyin cookies imported. Account verified: nickname=xxx, douyin_id=xxx"

## 🖥️ Supported Commands

Use the bundled collector script:

```bash
python3 social/common/scripts/social_creator_collect.py [command]
```

| Command | What it does |
|---|---|
| `init-account --account <name>` | Create folder structure |
| `bilibili-probe --account <name>` | Verify B站 login + account identity |
| `bilibili-summary --account <name> --days 30` | Fetch fan trend + recent video metrics |
| `check-douyin-cookies --account <name>` | Validate Douyin cookie JSON structure |
| `import-douyin-cookies --account <name> --nickname "..." --douyin-id "..." --headless` | Import cookies → Playwright storage state, verify account |
| `douyin-worklist --account <name> --days 30 --headless` | Collect creator-center works + fan growth per video |

## 📊 Douyin Per-Video Fan Growth

The Douyin creator center does NOT expose per-video fan growth via API. You must extract it from the DOM.

After running `douyin-worklist` and getting basic work list data, do:

```python
# Navigate to data-center/content, click 投稿列表 tab, extract table
# Column order: 作品名称|发布时间, 审核状态, 播放量, 完播率, 5s完播率, 
#   封面点击率, 2s跳出率, 平均播放时长, 点赞量, 分享量, 评论量, 
#   收藏量, 主页访问量, 粉丝增量, 操作
```

Use Playwright to:
1. Go to `https://creator.douyin.com/creator-micro/data-center/content`
2. Click the `投稿列表` tab
3. Extract the table from the DOM (NOT from API — the fan column is DOM-only)
4. The `粉丝增量` column is per-video fan growth

See `skills/social-creator-data/SKILL.md` for the detailed extraction script.

## 📁 Output

All outputs go under `social/<account>/<platform>/`:
- `raw/` — JSON
- `processed/` — Markdown reports

## 🔗 Repo

```
https://github.com/BobXu2358/social-creator-collector
```
