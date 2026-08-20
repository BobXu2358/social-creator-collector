# Bilibili Huahuo Order Field Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a privacy-safe `videos[].platform_fields.is_huahuo_order` field to `bilibili summary` by joining complete Huahuo order history on BVID.

**Architecture:** A bounded HTTP pagination helper reads only the Huahuo order-list endpoint and returns a set of valid BVIDs plus aggregate diagnostics. `summary` applies the result to canonical video rows when acquisition is complete and omits the field when enrichment is unavailable.

**Tech Stack:** Python 3, httpx, unittest, JSON Schema, Markdown documentation.

---

### Task 1: Specify Huahuo pagination and privacy behavior

**Files:**
- Modify: `tests/test_collector.py`

**Step 1: Write failing helper tests**

Add sanitized fixtures for two pages, duplicate BVIDs, blank/malformed BVIDs,
and private fields such as order number, customer, and price.

**Step 2: Verify failure**

Run: `python3 -m unittest tests.test_collector.PureParsers.test_bilibili_huahuo_orders_paginate_and_minimize -v`

Expected: FAIL because the Huahuo helper does not exist.

### Task 2: Implement complete Huahuo acquisition

**Files:**
- Modify: `collector/bilibili.py`
- Test: `tests/test_collector.py`

**Step 1: Add the minimal helper**

Request `GET https://cm.bilibili.com/commercialorder/api/web_api/v1/upper/order/list`
with `page` and `size`, validate `code/status/result`, follow `result.total`, and
return only valid BVID membership plus aggregate counts.

**Step 2: Run helper tests**

Run: `python3 -m unittest tests.test_collector.PureParsers.test_bilibili_huahuo_orders_paginate_and_minimize -v`

Expected: PASS.

### Task 3: Enrich summary rows with presence-safe semantics

**Files:**
- Modify: `collector/bilibili.py`
- Modify: `tests/test_collector.py`
- Modify: `schemas/collector-output.schema.json`

**Step 1: Write failing summary tests**

Cover true and false matches after a complete response, field omission after an
optional acquisition failure, aggregate diagnostics, and the absence of private
Huahuo values from serialized output.

**Step 2: Implement enrichment**

Fetch Huahuo history once per summary, attach
`platform_fields.is_huahuo_order` only when complete, add runtime field notes,
and preserve existing `platform_fields` values.

**Step 3: Run focused tests**

Run: `python3 -m unittest tests.test_collector -v`

Expected: all collector tests pass.

### Task 4: Update operator and consumer documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/CLI_REFERENCE.md`
- Modify: `AGENTS.md`
- Modify: `MAINTAINING.md`
- Modify: `skills/social-creator-data/SKILL.md`

**Step 1: Document the contract**

Record the exact field path, boolean type, direct Huahuo/BVID provenance,
true/false/absent semantics, account context, privacy exclusions, and a sanitized
consumer branch example. Add `cm.bilibili.com` as the read-only creator-center
business-data host required by the supported feature.

**Step 2: Search for stale summaries**

Run: `rg -n "Bilibili summary|bilibili summary|花火|is_huahuo_order" README.md AGENTS.md docs skills MAINTAINING.md schemas`

Expected: every command summary and owning reference is consistent.

### Task 5: Verify and publish

**Files:**
- Verify all modified files only.

**Step 1: Run verification**

Run:

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q collector tests
git diff --check
```

Expected: tests pass, compilation succeeds, and the diff check is clean.

**Step 2: Run a privacy-safe live check**

Use the existing local test account and report only total orders, matched summary
videos, and field counts. Never print cookies, raw orders, BVIDs, customer names,
brands, amounts, or order IDs.

**Step 3: Publish**

Stage only the intended implementation, test, schema, documentation, skill, and
plan files; commit; push `codex/bilibili-huahuo-order-field`; and create one draft
PR against `main` with verification evidence and privacy/failure semantics.
