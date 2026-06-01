---
name: feedback-analytics
description: Collect and analyze audience feedback on published B站 and 抖音 videos — comments (评论) and Bilibili danmaku (弹幕). Use when a user wants comment sentiment/themes, danmaku density-peak attribution, or a feedback report for a video, including cross-platform or comment-vs-danmaku comparison. Cookie-authenticated, read-only.
---

# Feedback Analytics

Collect comments and danmaku for *published* videos, then turn them into a structured
feedback report. Collection is bundled in the collector CLI; analysis is an LLM task you do
on the raw JSON.

## Collection

```bash
# B站 comments (needs a login cookie — anonymous calls return ~3 comments or 412)
python -m collector bilibili comments --account <account> --bvid BVxxx [--max-pages 10]

# B站 danmaku (fetch + density-peak analysis; public videos need no cookie)
python -m collector bilibili danmaku  --account <account> --bvid BVxxx \
    [--bucket-s 10] [--peak-n 5] [--peak-method topn|zscore] [--no-filter]

# 抖音 comments (Playwright intercepts the comment-list API the page itself signs)
python -m collector douyin comments   --account <account> --aweme-id ID [--max-pages 20]
```

Raw JSON lands in `social/<account>/<platform>/raw/`; the danmaku command also writes a
`processed/*.md` peak report.

### Why these specific paths

- **B站 comments** use `x/v2/reply/main` (mode=3, cursor pagination), **not**
  `x/v2/reply/wbi/main` which silently returns only ~3 hot comments. The opaque
  `cursor.pagination_reply.next_offset` is the real cursor — `pn=N` looks like it paginates
  but returns page 1 every time. Comments are deduped by `rpid`.
- **B站 danmaku** comes from the deflate-compressed XML endpoint (`x/v1/dm/list.so`),
  decompressed with `zlib.decompress(raw, -MAX_WBITS)`. `cid` ≠ `aid`; multi-part videos
  have one cid per part (the command handles both). pool=1 is subtitle danmaku, filtered by default.
- **抖音 comments** have no clean REST endpoint — the collector loads the video page with the
  imported storage state and captures `aweme/v1/web/comment/list` responses, scrolling to
  lazy-load more. Douyin's DOM class names rotate, so it scrolls the tallest scrollable element
  rather than a fixed selector.

## Comment feedback analysis

This is a text/LLM task on the raw comments — not a numeric one. Produce:

1. **Sentiment overview** — rough positive/neutral/negative split (not exact counts).
2. **Top themes (3–5)** — recurring topics grouped by subject.
3. **Representative quotes (2–3 per theme)** — verbatim, with `uname`/nickname. Never paraphrase
   a comment into something the user didn't say.
4. **Actionable feedback** — corrections, requests, suggestions the creator could act on. Flag
   comments that correct factual errors or request follow-up content.
5. **Cross-platform comparison** (if both collected) — where B站 and 抖音 audiences diverge.

Distinguish a loud high-like minority from broad consensus — a few top comments ≠ majority view.

### Suggested report shape

```markdown
# 评论反馈分析：《视频标题》
## 概览：平台 / 评论数 / 情绪分布（约 X% 正面…）
## 核心主题
### 主题一：…（约 N 条）
> "评论原文" — @用户名
分析：一句话
## 可行动反馈
- …
## 跨平台对比（如有）
| 维度 | B站 | 抖音 |
```

## Danmaku peak interpretation

The danmaku command already computes density peaks (time-bucketed), per-peak keywords, and
sample quotes. Your job is **attribution**: for each peak, explain *why* viewers reacted there
(what the video showed at that timestamp), and compare danmaku peaks against comment themes —
danmaku captures in-the-moment reactions, comments capture considered takes.

- Low-count videos (< ~50 danmaku): say "insufficient data" rather than over-reading peaks.
- Use `--peak-method zscore` for high-volume videos, `topn` (default) for smaller ones.

## Known pitfalls

- **Cookie expiry** is the usual cause of empty results — verify login before debugging.
- **B站 `cursor.all_count` includes sub-replies**, so it's always higher than the top-level
  count you can collect; don't treat it as a target.
- **Rate limiting**: the collector spaces B站 comment pages (~600ms) and 抖音 scrolls (~1.5s).
  Don't tighten these.
- **抖音 comment API changes** — if interception returns nothing, the endpoint or page changed;
  re-inspect network traffic in a headed browser.
