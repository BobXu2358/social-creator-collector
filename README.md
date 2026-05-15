# Social Creator Collector

Read-only Bilibili & Douyin creator-center data toolkit for AI agents (Claude Code, Codex, Cursor, OpenClaw, etc.).

## Quick Start (for humans)

Tell your agent:

> Go to https://github.com/BobXu2358/social-creator-collector, read AGENTS.md, and set up the social creator data collector for me.

Your agent will:
1. Clone this repo
2. Install Python dependencies
3. Ask you to export cookies with Cookie-Editor
4. Collect read-only creator data

## What's inside

| File | Who reads it |
|---|---|
| `AGENTS.md` | Your AI agent — main entry point |
| `CLAUDE.md` / `CODEX.md` | Claude Code / Codex specific |
| `skills/` | Agent skills (detailed workflows) |
| `scripts/` | The Python collector |
| `example-credentials/` | Templates for cookie files |

## Safety

- **Read-only** — never posts, edits, deletes, or changes account settings
- Cookies stay in local files, never in chat
- One account per business namespace

## Cookie Editor

Install this Chrome extension to export cookies:
https://chromewebstore.google.com/detail/cookie-editor/hlkenndednhfkekhgcdicdfddnkalmdm
