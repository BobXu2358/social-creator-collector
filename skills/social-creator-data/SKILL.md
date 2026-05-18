---
name: social-creator-data
description: Collect read-only Bilibili and Douyin creator-center data including per-video fan growth. Use when a user asks to set up, onboard, verify, or run B站/哔哩哔哩 or 抖音 creator backend data collection, especially with Cookie-Editor exports, Bilibili SESSDATA/bili_jct/buvid3, Douyin creator center cookies, work lists, trends, archive stats, per-video 粉丝增量/涨粉 extraction, or cross-platform creator analytics.
---
# Social Creator Data

Use this skill to set up and operate the bundled read-only collector for Bilibili and Douyin creator data, including **per-video fan growth extraction** from Douyin's 投稿列表 DOM.

## 🔗 Recommended Browser Extension

```
https://chromewebstore.google.com/detail/cookie-editor/hlkenndednhfkekhgcdicdfddnkalmdm
```

## Safety boundaries

- Read-only only: no posting, editing, deleting, commenting, replying, DM, following, or account settings.
- Never ask the user to paste cookies into chat. Offer two modes: local file (preferred) or chat paste (fallback, see below).
- Never print cookie values, tokens, storage state, browser profile contents, or screenshots containing secrets.
- Keep one account namespace per creator/business.
- Do not index `_secrets/`, browser profiles, or raw login state into memory.

## Tool/script

The bundled collector is at:

```bash
python3 social/common/scripts/social_creator_collect.py --help
```

## Onboarding flow

### 0. Install Cookie-Editor

Send this link to the human:
```
https://chromewebstore.google.com/detail/cookie-editor/hlkenndednhfkekhgcdicdfddnkalmdm
```

### 1. Initialize account folders

```bash
python3 social/common/scripts/social_creator_collect.py init-account --account <account>
```

### 2. Cookie Onboarding — Two Modes

#### Mode A: Local file (preferred)

Ask the human to export cookies as JSON (NOT Netscape text format) and save:

- B站: `social/_secrets/<account>/bilibili/default.credentials.json`
  ```json
  {"SESSDATA":"...","bili_jct":"...","buvid3":"..."}
  ```
- 抖音: `social/_secrets/<account>/douyin/default.cookies.json`
  (Cookie-Editor export → JSON)

#### Mode B: Chat paste (fallback)

If the human pastes cookie JSON in chat:
1. Read the JSON they pasted
2. Strip unnecessary fields — keep only essential keys:
   - B站: `SESSDATA`, `bili_jct`, `buvid3`
   - 抖音: `sessionid`, `sessionid_ss`, `sid_guard`, `uid_tt`, `passport_csrf_token`, `passport_csrf_token_default`, `sid_tt`, `ssid_ucp_v1`, `uid_tt_ss`
3. Write the cleaned JSON to the appropriate `_secrets` path
4. Set `chmod 600` on the file
5. NEVER echo or re-display cookie values
6. Reply only with verification results (mid, nickname, douyin_id — not cookie data)

### 3. Bilibili verify + collect

```bash
python3 social/common/scripts/social_creator_collect.py bilibili-probe --account <account>
python3 social/common/scripts/social_creator_collect.py bilibili-summary --account <account> --days 30
```

### 4. Douyin verify + collect

```bash
python3 social/common/scripts/social_creator_collect.py check-douyin-cookies --account <account>
python3 social/common/scripts/social_creator_collect.py import-douyin-cookies --account <account> --nickname "账号昵称" --douyin-id "抖音号" --headless
python3 social/common/scripts/social_creator_collect.py douyin-worklist --account <account> --days 30 --headless
```

## Douyin Per-Video Fan Growth (粉丝增量)

**Critical**: The Douyin creator center does NOT expose per-video fan growth via any API endpoint. It is only available in the DOM of the 投稿列表 page.

### Extraction method

1. Navigate to `https://creator.douyin.com/creator-micro/data-center/content`
2. Click the `投稿列表` tab
3. Extract the table from the DOM:

```python
from playwright.async_api import async_playwright

async def extract_douyin_fan_growth(storage_state_path, profile_dir):
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            executable_path='/usr/bin/chromium',
            headless=True,
            args=['--no-sandbox','--disable-dev-shm-usage','--disable-blink-features=AutomationControlled'],
            viewport={'width':1365,'height':900}, locale='zh-CN', timezone_id='Asia/Shanghai')
        # Load cookies
        import json
        s = json.loads(open(storage_state_path).read())
        if s.get('cookies'): await ctx.add_cookies(s['cookies'])
        
        page = await ctx.new_page()
        await page.goto('https://creator.douyin.com/creator-micro/data-center/content',
                        timeout=60000, wait_until='networkidle')
        await page.wait_for_timeout(4000)
        await page.locator('text=投稿列表').first.click()
        await page.wait_for_timeout(5000)

        # Extract table
        rows = await page.evaluate('''()=>{
            const cells=document.querySelectorAll('td,th');
            let result=[], currentRow=[], lastTop=-1;
            cells.forEach(c=>{
                const rect=c.getBoundingClientRect();
                if(rect.top!==lastTop && currentRow.length>0){
                    result.push([...currentRow]); currentRow=[];
                }
                currentRow.push(c.innerText?.trim()||'');
                lastTop=rect.top;
            });
            if(currentRow.length>0) result.push([...currentRow]);
            return result;
        }''')
        
        # Header: 作品名称|发布时间, 审核状态, 播放量, 完播率, 5s完播率,
        #   封面点击率, 2s跳出率, 平均播放时长, 点赞量, 分享量, 评论量,
        #   收藏量, 主页访问量, 粉丝增量, 操作
        # Column index 13 (0-based) = 粉丝增量
        header = rows[0]
        fan_idx = next(i for i,h in enumerate(header) if '粉丝增量' in h)
        for row in rows[1:]:
            title = row[0].split('\n')[0]  # first line is title
            date = row[0].split('\n')[1] if '\n' in row[0] else ''
            fan_growth = row[fan_idx]  # e.g. "76", "2383", "6516"
            print(f'{date} | {title[:50]} | fan_growth={fan_growth}')
        
        await ctx.close()
```

The `粉丝增量` column (index 13, 0-based) contains the per-video fan growth count as a string like `"76"`, `"2,383"`, or `"6,788"`. Parse as integer after removing commas.

### Column reference

| Index | Column |
|---|---|
| 0 | 作品名称 \| 发布时间 |
| 1 | 审核状态 |
| 2 | 播放量 |
| 3 | 完播率 |
| 4 | 5s完播率 |
| 5 | 封面点击率 |
| 6 | 2s跳出率 |
| 7 | 平均播放时长 |
| 8 | 点赞量 |
| 9 | 分享量 |
| 10 | 评论量 |
| 11 | 收藏量 |
| 12 | 主页访问量 |
| **13** | **粉丝增量** |
| 14 | 操作 |

## What the collector can do now

- Bilibili:
  - verify login/account routing;
  - collect recent fan trend and archive/work metrics from creator center;
  - output raw JSON + processed Markdown.
- Douyin:
  - validate Cookie-Editor JSON exports;
  - import cookies into Playwright storage state;
  - verify creator-center login against nickname/Douyin ID hints;
  - collect creator-center `work_list` pages and filter by recent days;
  - **extract per-video fan growth from 投稿列表 DOM** (no API — DOM only).

## Danmaku Analytics

For danmaku (弹幕) collection and peak analysis, use the `danmaku-analytics` skill. It handles deflate-compressed XML, time-bucket density, keyword extraction at peaks, and cross-format comparison.

## Comment Feedback Analysis

For collecting and analyzing comments on published videos, use the `comment-analytics` skill. It covers B站 comment API (`x/v2/reply/main` with cookie), 抖音 comment extraction, and structured feedback analysis (sentiment, themes, actionable feedback, cross-platform comparison). Key anti-pitfall: never use `x/v2/reply/wbi/main` (returns only 3 hot comments) or public/unauth endpoints (triggers 412).

## Known pitfalls

- Do not paste cookie values into chat (unless human initiates chat-paste mode).
- Bilibili direct public APIs can hit `412`, `-799`, `-352`, `-403`; use creator-center paths.
- Douyin public pages show only partial works; creator-center `work_list` is more complete.
- Douyin QR login from remote Playwright/headless sessions is rejected as expired. Use local browser + Cookie-Editor export.
- Playwright `launch_persistent_context` does not accept `storage_state`; use `add_cookies()` instead.
- Douyin per-video fan growth is DOM-only, not in any API response.
- Never store or forward transient STS/upload/IM tokens that appear in creator-center network logs.
