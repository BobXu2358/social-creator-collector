---
name: danmaku-analytics
description: Scrape Bilibili danmaku (弹幕) and analyze density peaks, content themes, and viewer reaction attribution. Use when an agent needs to understand where and why viewers reacted most intensely in a video.
---

# Danmaku Analytics

Scrape B站 danmaku data and produce peak-density analyses with content attribution. Works for any logged-in B站 account.

## Anti-412 and Anti-Pitfall Design

The danmaku XML endpoint (`/x/v1/dm/list.so`) returns deflate-compressed XML. It is more reliable than the protobuf segment endpoint and works without authentication for public videos. Rate limiting is per-IP; space requests ≥500ms apart.

```
GET https://api.bilibili.com/x/v1/dm/list.so?oid=<cid>
```

Returns: deflate-compressed XML. Decompress with `zlib.decompress(data, -zlib.MAX_WBITS)`.

Each danmaku entry:
```xml
<d p="time,type,size,color,ctime,pool,sender_hash,row_id">content</d>
```

p attribute (comma-separated):
- `time`: seconds into video (float, e.g. `374.5`)
- `type`: 0=scroll, 1=top, 2=bottom, 4=bottom-fixed, 5=special
- `size`: font size (18/25/36 etc.)
- `color`: decimal RGB color
- `ctime`: unix timestamp of posting
- `pool`: 0=normal, 1=subtitle, 2=special pool
- `sender_hash`: hashed user id
- `row_id`: database row id (unique per danmaku)

## How to Get the cid

The `cid` is different from `aid`/`bvid`. Get it from the video info endpoint:

```
GET https://api.bilibili.com/x/web-interface/view?bvid=<bvid>
```

Response: `data.cid` for single-part videos, or `data.pages[].cid` for multi-part.

## Collection Script

```python
import urllib.request
import zlib
import re
import json
from collections import Counter, defaultdict
from pathlib import Path

def fetch_danmaku(cid: int) -> list[dict]:
    """Fetch danmaku for a video cid. Returns list of {time_s, content, type, color, ctime, pool}."""
    url = f"https://api.bilibili.com/x/v1/dm/list.so?oid={cid}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": f"https://www.bilibili.com/video/av{cid}",
    })
    resp = urllib.request.urlopen(req)
    raw = resp.read()
    data = zlib.decompress(raw, -zlib.MAX_WBITS)
    text = data.decode('utf-8', errors='replace')

    dms = []
    for match in re.finditer(r'<d p="([^"]*)">(.*?)</d>', text):
        attrs = match.group(1).split(',')
        dms.append({
            "time_s": float(attrs[0]) if attrs else 0,
            "type": int(attrs[1]) if len(attrs) > 1 else 0,
            "size": int(attrs[2]) if len(attrs) > 2 else 25,
            "color": int(attrs[3]) if len(attrs) > 3 else 0xFFFFFF,
            "ctime": int(attrs[4]) if len(attrs) > 4 else 0,
            "pool": int(attrs[5]) if len(attrs) > 5 else 0,
            "content": match.group(2),
        })
    return dms
```

## Peak Detection and Analysis

### Step 1: Time-bucket density

Segment danmaku into fixed intervals (default: 10s). Count danmaku per bucket.

```python
BUCKET_S = 10
buckets = Counter()
bucket_content = defaultdict(list)

for dm in danmaku:
    key = int(dm["time_s"] // BUCKET_S) * BUCKET_S
    buckets[key] += 1
    bucket_content[key].append(dm["content"])
```

### Step 2: Find peaks

Two methods, choose based on danmaku volume:

- **Top-N** (small videos < 500 danmaku): take top 5-8 buckets by count
- **Z-score** (large videos > 500 danmaku): buckets where `(count - mean) / stddev > 2.0`

```python
def find_peaks(buckets: Counter, method: str = "topn", n: int = 5) -> list[tuple[int, int]]:
    if method == "topn":
        return buckets.most_common(n)
    # Z-score method
    import statistics
    counts = list(buckets.values())
    mean = statistics.mean(counts)
    stdev = statistics.stdev(counts) if len(counts) > 1 else 1
    peaks = [(ts, count) for ts, count in buckets.items()
             if (count - mean) / stdev > 2.0]
    return sorted(peaks, key=lambda x: -x[1])
```

### Step 3: Extract themes at peaks

For each peak bucket, extract keywords using simple frequency analysis:

```python
import re
def extract_keywords(texts: list[str], top_n: int = 8) -> list[tuple[str, int]]:
    """Simple keyword extraction: 2-4 char CJK bigrams + meaningful tokens."""
    words = Counter()
    for text in texts:
        # CJK bigrams
        cjk = re.findall(r'[\u4e00-\u9fff]{2,4}', text)
        for w in cjk:
            if len(w) >= 2:
                words[w] += 1
    return words.most_common(top_n)
```

### Step 4: Sample quotes

Pick 3-5 representative danmaku from each peak for the report:

```python
def sample_quotes(contents: list[str], n: int = 5) -> list[str]:
    # Prefer longer, more substantive danmaku
    ranked = sorted(contents, key=lambda x: -len(x))
    return ranked[:n]
```

## Output Format

```markdown
# 弹幕分析：《视频标题》

## 概览
- 总弹幕数：N 条
- 视频时长：M 分
- 弹幕密度：X 条/分钟
- 峰值数：Y 个时间段

## 弹幕密度峰值

### 峰值 1：[时间区间]（N 条弹幕）
**关键话题：** XXX、YYY、ZZZ
**代表性弹幕：**
- "原文" 
- "原文"
**归因分析：** 观众在此处集中讨论 XXX，可能因为视频这一段提到了 YYY...

### 峰值 2：[时间区间]（N 条弹幕）
...

## 弹幕内容分析
- 整体情绪倾向：...
- 高频关键词：XXX(N 次)、YYY(N 次)
- 特殊现象：（如大规模刷屏、梗传播、争议点）

## 与评论对比
- 弹幕与评论关注点差异：...
- 弹幕独特发现：...
```

## Known Pitfalls

1. **XML is deflate-compressed.** Not gzip, not raw XML. Must use `zlib.decompress(data, -zlib.MAX_WBITS)`.
2. **cid is not aid.** Always get cid from `/x/web-interface/view?bvid=...` first.
3. **Multi-part videos**: each part has its own cid; iterate `data.pages[]`.
4. **Low-count videos** (< 50 danmaku): peak analysis is unreliable; report "insufficient data" instead.
5. **Danmaku pool filter**: pool=0 is normal, pool=1 is subtitle danmaku (often auto-generated). Filter pool=1 out for organic analysis.
6. **Large videos** (> 5000 danmaku): the single XML endpoint may be truncated. Use the protobuf segment endpoint instead (`/x/v2/dm/web/seg.so?type=1&oid=<cid>&segment_index=<n>`), which requires protobuf parsing of `DmWebViewReply`.
7. **No authentication needed for public videos.** The XML endpoint works without cookie. If rate-limited, add SESSDATA cookie.
8. **Timestamp precision**: 0.1s. Bucket sizes below 5s may produce noisy results for low-density videos.

## Integration with Other Skills

- `social-creator-data`: get video list and metadata (bvid, aid, cid, title)
- `comment-analytics`: compare danmaku peaks with comment themes for cross-format insights
- Typical flow: collect video list → for each video, fetch danmaku → analyze peaks → compare with comments → produce integrated report
