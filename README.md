# Social Creator Collector

为 AI agent(Claude Code、Codex、Cursor、OpenClaw 等)提供的只读 Bilibili & Douyin 创作中心数据工具包。

## Quick Start(给人类看的)

告诉你的 agent:

> 去 https://github.com/BobXu2358/social-creator-collector,读 AGENTS.md,帮我把这个内容创作者数据采集器配置好。

你的 agent 会:

1. Clone 此 repo
2. 安装 Python 依赖
3. 引导你用 Cookie-Editor 导出 cookies
4. 采集只读的内容创作者数据

## 仓库内容

| 文件 | 读取者 |
|---|---|
| `AGENTS.md` | 你的 AI agent —— 主入口 |
| `CLAUDE.md` / `CODEX.md` | Claude Code / Codex 专用 |
| `skills/` | Agent skills(详细工作流) |
| `scripts/` | Python 采集器 |
| `example-credentials/` | cookie 文件模板 |

## 安全性

- **只读** —— 绝不 post、编辑、删除内容,也不修改账号设置
- Cookies 仅保存在本地文件中,不会出现在对话里
- 每个 business namespace 下只对应一个账号

## Cookie Editor

安装此 Chrome extension 来导出 cookies:
https://chromewebstore.google.com/detail/cookie-editor/hlkenndednhfkekhgcdicdfddnkalmdm
