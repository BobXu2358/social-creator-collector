# Bilibili Huahuo Order Field Design

## Goal

Let a consumer of `bilibili summary` determine whether each returned video is
linked to a Bilibili Huahuo order, using the platform's direct BVID association
instead of title, date, hashtag, brand, or other heuristics.

## Considered approaches

1. **Enrich `bilibili summary` rows (selected).** Fetch the creator's complete
   Huahuo order list once, index orders by `bv_id`, and attach a precise
   Bilibili-specific boolean to each summary video. This directly serves the
   stated workflow and keeps the join inside the collector.
2. **Add a separate `huahuo-orders` command.** This keeps acquisition separate,
   but every consumer would have to persist and join two outputs. It adds a CLI
   surface without improving the requested per-video decision.
3. **Emit a generic `is_commercial` boolean.** This is misleading: Huahuo can
   confirm a platform order, but failure to find one cannot rule out an
   off-platform commercial deal.

## Contract and data flow

`bilibili summary` will request the creator-center Huahuo order list from
`cm.bilibili.com` over the existing authenticated HTTP client. The request uses
only `page` and `size`, follows `result.total`, and stops only after all reported
rows are collected. Valid, non-empty `bv_id` values are kept in memory; order
numbers, customer names, brands, prices, creator identity, and other private
business fields are never emitted or persisted.

When the complete order list is available, every returned Bilibili video gets
`videos[].platform_fields.is_huahuo_order`:

- `true`: at least one Huahuo order row has `bv_id == content_id`;
- `false`: the complete order response succeeded but contained no matching
  `bv_id`;
- absent: Huahuo acquisition was unavailable, malformed, incomplete, or failed.

`false` means only "no matching Huahuo order in the successfully retrieved
account history". It must not be interpreted as the broader claim that the
video was not commercial.

## Failure, privacy, and verification

Huahuo enrichment is optional and fail-soft so accounts without Huahuo access
do not lose their normal summary. A compact diagnostic records whether the
order history was available and, on success, only aggregate order/match counts.
No raw exception text, order payload, BVID list, query string, or identity data
is added to output.

Offline tests cover pagination, valid/missing/malformed `bv_id`, duplicate
orders, successful true/false enrichment, unavailable-field omission, and the
absence of private order fields. Live verification uses a real creator account
only to confirm aggregate counts and matching behavior; credentials and order
contents remain unprinted.
