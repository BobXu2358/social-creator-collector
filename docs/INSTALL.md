# Install Social Creator Collector

## Contents

```text
plugin/              Optional OpenClaw native plugin skeleton exposing `social_creator_collect`
skill/               AgentSkill `social-creator-data`
agent-instructions/  Portable AGENTS.md / CLAUDE.md / CODEX.md prompts for non-OpenClaw agents
package/             Python collector package and examples
docs/                Agent handoff and install notes
```

## For Claude / Codex / non-OpenClaw agents

Use the Python collector directly. The OpenClaw plugin is optional and not needed.

Copy one of these instruction files into the target agent workspace if useful:

```text
agent-instructions/AGENTS.md
agent-instructions/CLAUDE.md
agent-instructions/CODEX.md
```

See `docs/NON_OPENCLAW_AGENTS.md` for the full non-OpenClaw onboarding flow.

## Minimal copy install

From this deliverable root, in the target OpenClaw workspace:

```bash
mkdir -p tools/social-creator-collector
cp package/social-creator-collector/scripts/social_creator_collect.py tools/social-creator-collector/
chmod +x tools/social-creator-collector/social_creator_collect.py

mkdir -p skills/social-creator-data
cp -R skill/social-creator-data/* skills/social-creator-data/

mkdir -p social/common/examples social/common/schemas
cp package/social-creator-collector/social/common/examples/* social/common/examples/
cp package/social-creator-collector/social/common/schemas/* social/common/schemas/
cp package/social-creator-collector/social/requirements.txt social/requirements.txt
```

Install dependencies if the workspace does not already have them:

```bash
python3 -m venv social/.venv
social/.venv/bin/pip install -r social/requirements.txt playwright httpx
```

If Chromium is not installed, ask the human before downloading a Playwright browser:

```bash
social/.venv/bin/python -m playwright install chromium
```

## Optional OpenClaw plugin install

The plugin wrapper is a skeleton native OpenClaw plugin. It expects the Python script at:

```text
tools/social-creator-collector/social_creator_collect.py
```

Install from local path:

```bash
openclaw plugins install ./plugin
openclaw gateway restart
openclaw plugins inspect social-creator-collector --runtime --json
```

Then the agent tool should appear as:

```text
social_creator_collect
```

If the plugin is not installed, agents can still use the Skill and run the Python script directly via shell.

## First run

```bash
python3 tools/social-creator-collector/social_creator_collect.py init-account --account demo
```

Then follow `docs/AGENT_HANDOFF.md` or the `social-creator-data` skill.
