---
name: comment-analytics
description: Scrape read-only comments from Bilibili and Douyin, then analyze feedback themes, sentiment, and user reactions. Use when an agent needs to collect comment data or produce a comment-feedback report for a published video, including cross-platform comparison. Works with cookie-authenticated sessions — not public/anonymous endpoints.
---

# Comment Analytics

Scrape and analyze comments for published videos on B站 and 抖音. This skill is business-generic: it works for any account that has a valid login cookie.

## Anti-412 and Anti-Pitfall Design

**Golden rule**: never hit B站 comment endpoints without a valid login cookie. Public/anonymous comment APIs are rate-limited, return only 3 top comments, and trigger `412` risk control. The same applies to 抖音 — public page scraping is unreliable.

### B站 correct endpoint

```
GET https://api.bilibili.com/x/v2/reply/main?type=1&oid=<aid>&mode=3&ps=20&pn=1
```

Key parameters:
- `type=1`: video comment
- `oid`: the video `aid` (not `bvid`; convert `bvid` to `aid` first)
- `mode=3`: newest-first ordering (most useful for feedback analysis)
- `ps=20`: page size (max 20)
- `pn`: page number

Required headers:
- `Cookie: SESSDATA=...; bili_jct=...`
- `User-Agent`: standard desktop Chrome UA
- `Referer: https://www.bilibili.com/video/<bvid>`

Response fields:
- `data.replies[]`: array of comment objects
- `data.page.count`: current page count
- `data.page.num`: current page number
- `data.page.size`: page size
- `data.cursor.all_count`: total comment count (includes sub-replies)
- `data.cursor.pagination_reply`: pagination control, `next_offset` for page 2+

### B站 wrong endpoint (⚠️ DO NOT USE)

- `GET https://api.bilibili.com/x/v2/reply/wbi/main?...` — returns only 3 top/hot comments, regardless of `mode` parameter. This looks like it works but silently drops 99% of comments.
- Any endpoint without cookie header — triggers `412` or returns empty.

### 抖音 correct path

抖音 comments are not available through a simple REST endpoint. Use Playwright with an imported storage state:

1. Import cookies from Cookie-Editor JSON into Playwright `storage_state.json`
2. Launch persistent context with `add_cookies()`
3. Navigate to `https://creator.douyin.com/creator-micro/data-center/content`
4. For each video, click into detail view or intercept the comment-list network request
5. Alternatively, use the mobile-friendly comment endpoint discovered by monitoring network traffic in creator center

The comment extraction flow is page-driven, not API-driven. See `social-creator-data` skill for full Playwright setup.

## Comment Collection Workflow

### Step 1: Determine target video(s)

Get `aid` (B站) or `aweme_id` (抖音) from the video. For B站:
```python
# bvid → aid conversion
import re
table = "fZodR9XQDSUm21yCkr6zBqiveYah8bt4xsWpHnJE7jL5VG3guMTKNPAwcF"
tr = {table[i]: i for i in range(58)}
s = [11, 10, 3, 8, 4, 6]
xor = 177451812
add = 8728348608

def bvid_to_aid(bvid: str) -> int:
    r = sum(tr[bvid[s[i]]] * (58 ** i) for i in range(6))
    return (r - add) ^ xor
```

### Step 2: B站 — paginate comments

```python
import httpx

def fetch_comments(sessdata: str, aid: int, max_pages: int = 10) -> list[dict]:
    all_comments = []
    cookies = {"SESSDATA": sessdata}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ...",
        "Referer": f"https://www.bilibili.com/video/av{aid}",
    }
    for pn in range(1, max_pages + 1):
        url = f"https://api.bilibili.com/x/v2/reply/main?type=1&oid={aid}&mode=3&ps=20&pn={pn}"
        resp = httpx.get(url, cookies=cookies, headers=headers, timeout=30)
        data = resp.json()
        replies = data.get("data", {}).get("replies") or []
        if not replies:
            break
        for r in replies:
            all_comments.append({
                "rpid": r.get("rpid"),
                "mid": r.get("mid"),
                "uname": r.get("member", {}).get("uname"),
                "message": r.get("content", {}).get("message"),
                "ctime": r.get("ctime"),
                "like": r.get("like"),
                "rcount": r.get("rcount"),
            })
        cursor = data.get("data", {}).get("cursor", {})
        # Stop if all_count reached
        if len(all_comments) >= cursor.get("all_count", 0):
            break
    return all_comments
```

### Step 3: 抖音 — comment extraction

Use the `social-creator-data` skill's Playwright setup. After navigating to creator center data content page, the comment API endpoint can be discovered via network monitoring. Current known path (may change):

Monitor for requests matching `aweme/v1/web/comment/list/` and capture the response.

### Step 4: Save raw and processed

```text
social/<business>/<platform>/
├── raw/<platform>-video-comments-<content_id>-<timestamp>.json
└── processed/<platform>-video-comments-<content_id>-<timestamp>.md
```

## Comment Feedback Analysis

After collecting raw comments, produce a structured analysis. This is a text/LLM analysis task, not a numeric one.

### Analysis dimensions

1. **Sentiment overview**: positive / neutral / negative ratio (approximate, not exact count)
2. **Top themes** (3-5): recurring topics, grouped by subject matter
3. **Representative quotes** (2-3 per theme): verbatim comments with `uname`
4. **Actionable feedback**: suggestions, corrections, requests that the creator could act on
5. **Cross-platform comparison** (if both B站 and 抖音 collected): differences in audience reaction, platform-specific concerns

### Output format

```markdown
# 评论反馈分析：《视频标题》

## 概览
- 平台：B站/抖音
- 评论数：N 条（顶层）
- 情绪分布：正面约 X%，中性约 Y%，负面约 Z%

## 核心主题
### 主题一：XXX
- 热度：约 N 条相关
- 代表性评论：
  > "评论原文" — @用户名
- 分析：一句话总结

### 主题二：...
...

## 可行动反馈
- [具体建议 1]
- [具体建议 2]

## 跨平台对比（如有）
| 维度 | B站 | 抖音 |
| --- | --- | --- |
| 情绪倾向 | ... | ... |
| 核心争议 | ... | ... |
```

### Analysis principles

- Always quote actual comments; never fabricate or paraphrase into something the user didn't say.
- Note when a theme appears in both platforms vs platform-specific.
- Distinguish "loud minority" from "broad consensus" — a few high-like comments ≠ majority view.
- Flag comments that correct factual errors in the video.
- Flag comments that request follow-up content.

## Known Pitfalls

1. **B站 `x/v2/reply/wbi/main` returns only 3 hot comments.** Always use `x/v2/reply/main` (no `wbi`) with cookie.
2. **Public/anonymous calls trigger 412.** Never attempt B站 comment collection without login cookie. If 412 appears, do NOT retry — check cookie validity first.
3. **B站 all_count includes sub-replies.** The `cursor.all_count` field counts total replies including nested sub-replies, so it will always be higher than the number of top-level comments you can collect. Don't expect to reach `all_count`.
4. **抖音 comment API changes frequently.** The page structure and network endpoints are not stable. Always verify with live network monitoring before building automation.
5. **Cookie expiry.** B站 SESSDATA and 抖音 session cookies expire. If collection returns empty/error, verify login first before debugging code.
6. **Rate limiting.** Space out requests: ≥500ms between pages for B站 comments, ≥2s between actions for 抖音 Playwright.
7. **Agent delegation.** Never expose raw cookies to business agents. Keep login state in main/privileged agent; business agents request read-only extraction via queue or `sessions_send`.

## Integration with Other Skills

- `social-creator-data`: use for cookie onboarding, Playwright setup, account verification, video list collection
- `bilibili-creator-data`: use for B站-specific credential management and creator-center endpoints
- Typical flow: `social-creator-data` verifies login → `comment-analytics` collects + analyzes → business agent consumes report

## Bundled Script

A reference script `scripts/collect_comments.py` is provided for B站 comment collection. See `--help` for usage.
