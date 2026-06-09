"""Canonical, versioned output rows — the cross-platform contract consumers read.

Every command that emits per-video metrics or daily fan trends builds its rows
through here, so a consumer sees the SAME field names whether the data came from
Bilibili (httpx) or Douyin (DOM / page-signed fetch). This is the stateless
*contract*; stateful storage/history/query is a consumer concern and lives
downstream (see MAINTAINING.md "core vs glue").

Standard cross-platform metric keys (a row carries whatever applies; absent = N/A):

    plays  likes  comments  shares  collects  coins  fans

``fans`` is the per-video fan delta, but its definition differs by platform — keep
that in mind when comparing across platforms:
  * bilibili: cumulative fans attributed to the video (creator-center real_stat.fans)
  * douyin:   粉丝增量 within the 投稿列表 publish-time window (DOM)

Platform-specific extras (e.g. ``full_play_ratio``) may appear as additional metric
keys. ``content_id`` is null for Douyin fan-growth rows (the 投稿列表 DOM exposes no
aweme_id); those rows include a fallback ``join_key`` derived from title and
published_at. It is not a platform id, so prefer real ``content_id`` whenever one
is available.

Work metadata such as ``duration_s``, ``cover_url``, ``category``, ``tags``,
``work_type``, ``status``, ``visibility``, ``audit_status``, ``copyright``, and
``is_original`` may appear when a platform returns it. Unclear platform-native
fields can be preserved under ``platform_fields`` with notes at the command-result
level.

Per-video *detail* commands (``bilibili video-detail`` / ``douyin item-analysis``)
add already-normalized percent/seconds metric keys — ``avg_watch_duration_s``,
``avg_completion_pct``, ``completion_rate_5s_pct``, ``bounce_rate_2s_pct``,
``follower_play_ratio_pct``, ``guest_play_ratio_pct`` — and a free-form ``detail``
block (retention curve, audience split, terminal distribution, per-client splits).
All additive: same ``SCHEMA_VERSION`` (consumers using ``.get()`` are unaffected).
"""
from __future__ import annotations

from typing import Any

SCHEMA_VERSION = "1.0"


def video_row(*, platform: str, account: str, content_id: Any, title: str | None,
              published_at: str | None, captured_at: str, metrics: dict[str, Any],
              source_url: str | None = None) -> dict[str, Any]:
    """A per-video metrics row. ``metrics`` keeps only non-null values."""
    return {
        "schema_version": SCHEMA_VERSION,
        "platform": platform,
        "account": account,
        "content_id": str(content_id) if content_id not in (None, "") else None,
        "title": title,
        "published_at": published_at,
        "captured_at": captured_at,
        "source_url": source_url,
        "metrics": {k: v for k, v in metrics.items() if v is not None},
    }


def fan_trend_row(*, platform: str, account: str, date: str, fan_inc: int,
                  captured_at: str) -> dict[str, Any]:
    """A single day's net fan change (涨粉) for an account."""
    return {
        "schema_version": SCHEMA_VERSION,
        "platform": platform,
        "account": account,
        "date": date,
        "fan_inc": fan_inc,
        "captured_at": captured_at,
    }
