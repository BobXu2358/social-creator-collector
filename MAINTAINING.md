# Maintaining

This is the shared core. The point of keeping it in one repo is that **one fix heals
every consumer** — so changes go here and flow out by install, never by local hacks.

## Core vs glue (where things belong)

The line is **stateless contract → core; stateful state → consumer**.

- **In the core (this repo):** acquisition (the commands) and the *canonical row shape*
  (`collector/schema.py` + `schemas/collector-output.schema.json`). Generic, account-agnostic,
  no stored state.
- **Not in the core — each consumer builds its own:** the account registry, the historical
  store / database, retention, scheduling, dashboards, and any business-specific shaping.
  Route A means every team runs its own instance and keeps its own data; only the *schema*
  is shared so those stores stay row-compatible.

Don't add a database, retention policy, or business accounts to the core — that's the seam.

## Documentation ownership

- `README.md` is the human-facing overview and quick start.
- `AGENTS.md` owns safety, credentials, onboarding, and executable operator guidance.
- `docs/CLI_REFERENCE.md` owns the detailed command, option, output, and platform reference.
- `schemas/collector-output.schema.json` is the machine-readable canonical row contract.
- `skills/` contains task-specific collection and analysis workflows.

Link to the owning document instead of copying its full content into another file.

### Field documentation standard

Treat an output field as incomplete until a consumer can use it correctly without
reading the implementation. For every new or changed user-visible field, document:

1. **Path and scope** — the exact JSON path, which command/envelope emits it, and
   whether it is account-level, per-work, daily, or command-specific.
2. **Type, unit, and values** — including enums, percentage normalization, time zone,
   count semantics, and whether a platform-native value is preserved raw.
3. **Presence semantics** — distinguish absent, `null`, zero, `false`, and `unknown`.
   Never let consumers infer a negative fact merely because the platform omitted a field.
4. **Provenance and certainty** — say whether the value is directly reported by the
   platform, normalized by the collector, or inferred from multiple fields. Label
   unverified interpretations as inference instead of presenting them as an API contract.
5. **Context and privacy boundaries** — record whether a value is account-context-sensitive
   and which source identifiers or private fields must be compared only in memory and never
   emitted, logged, indexed, or copied between accounts.
6. **Consumer action** — when the field changes downstream behavior, include one short,
   sanitized example showing the safe branch/fallback. Examples must not contain real
   account IDs, work IDs, cookies, tokens, signed URLs, nicknames, or private payloads.

Put the detail in the owning surface and keep summaries short:

| Change | Required documentation |
|---|---|
| Canonical or command output field | `docs/CLI_REFERENCE.md`, runtime `field_notes`, tests; update the JSON Schema when its explicit contract changes |
| Headline capability or quick-start behavior | `README.md` summary plus a link to the CLI reference |
| Operator safety, account context, failure, or recovery behavior | `AGENTS.md` |
| Reusable collection/analysis decision | the affected file under `skills/` |
| Breaking contract change | schema version, this file's version history, CLI reference, migration note, and consumer tests |

An additive optional field does not require a schema-version bump, but it still requires
documentation of its presence semantics, a representative sanitized fixture, and tests for
present, absent, and malformed inputs. Do not document speculative fields before live evidence
confirms their path and meaning.

## Updating the core

It's a normal git-installed package. Consumers should pin a tag:

```bash
pip install "git+https://github.com/BobXu2358/social-creator-collector@<tag>"
```

To ship a change:
1. Branch → change → `pip install -e ".[dev]" && python -m unittest tests.test_collector`
   (offline tests must pass; CI runs them on every PR across Linux + Windows).
2. PR → review → merge to `main`.
3. Bump `collector/__init__.py` to the next package version.
4. **Tag a release**: `git tag vX.Y.Z && git push --tags`. Without a tag there's nothing to pin.
5. Consumers bump their pin and re-install.

### Documentation impact check (required before merge)

- [ ] If the PR changes user-visible behavior, a command, option, default, constraint,
      dependency, login flow, path, or failure mode, update the affected parts of
      `README.md`, `AGENTS.md`, and/or `docs/CLI_REFERENCE.md` in the same PR.
- [ ] If it adds, renames, removes, or changes the meaning or unit of an output field
      or envelope, update the schema description or version, CLI output reference,
      schema notes in this file, and affected skills in the same PR.
- [ ] For every affected field, document its exact path, type/unit or enum, presence
      semantics, provenance/certainty, account context, and privacy boundary.
- [ ] If the field changes downstream decisions, add or update one sanitized usage example;
      test present, absent, malformed, and privacy-sensitive cases where applicable.
- [ ] If it changes a maintenance, release, discovery, or security process, update
      `MAINTAINING.md` and `AGENTS.md` in the same PR.
- [ ] If it makes an example or platform comparison inaccurate, update every affected
      example and link in the same PR.
- [ ] Search all documentation and skills for the old term or field name, then run
      `git diff --check` and read the final diff as a consumer. Passing code tests alone
      does not prove that field ownership, units, or success/failure semantics are documented.

Documentation is part of the change, not a follow-up. Reviewers should not merge until
every applicable item is complete.

Live collection (real cookies, scraping) can't be tested in CI — verify those paths by hand
on a real account before tagging.

## Schema versioning

`SCHEMA_VERSION` in `collector/schema.py` is the consumer contract.

- `2.0`: Douyin `fan_trend` renamed overview `fans.option_list` output from
  `follower_plays` to `fan_total_on_date`, because the series is the account follower
  total for that date, not follower plays.

- **Additive** change (new optional metric key, new field) → same version. Consumers using
  `.get()` are unaffected.
- **Breaking** change (rename/remove a field, change a meaning) → bump `SCHEMA_VERSION` and
  note it here. Consumers can branch on `schema_version`.

## When Douyin breaks (it will)

`douyin fan-growth` reads a DOM table — the one fragile path, because 粉丝增量 has no API. On a
Douyin redesign it **fails loud** ("column not found" / "投稿列表 tab not found") rather than
returning wrong numbers. Playbook:

1. `douyin login --account <x>` (headed) and open `creator-micro/data-center/content` → 投稿列表.
2. Inspect the new structure (cells, the 粉丝增量 header, the scroll/lazy-load behaviour).
3. Fix the single function `_fan_growth` / `_EXTRACT_TABLE_JS` / `_parse_fan_table` in
   `collector/douyin.py`. Keep "locate the column by header text" — never hardcode a column index.
4. Re-run live, then PR → tag → consumers re-install. One fix, everyone healed.

Other page-signed or intercepted Douyin API paths and all Bilibili HTTP paths are more stable,
but Douyin's `work_list` magic params (`scene=star_atlas`, `aid=1128`, …) can also drift — they're
centralized in `collector/douyin.py`.
