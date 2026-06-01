# Social Creator Collector

只读的 Bilibili & 抖音创作者数据采集工具。一个 CLI 覆盖两个平台，专为 AI agent（Claude Code、Codex、Cursor 等）驱动设计，也可手动使用。

## 它能采什么

- **B站**（纯 HTTP）：登录校验、涨粉趋势 + 单稿数据（播放/涨粉/投币/评论/点赞）、视频评论、弹幕峰值分析。
- **抖音**（无头浏览器）：导入 cookie、作品列表、**单稿粉丝增量**（接口拿不到，从投稿列表 DOM 提取）、视频评论。

两个平台的数据路径不同是有原因的：B站三个 cookie 拼成请求头就够了，抖音的登录态和 `a-bogus` 签名只能在真浏览器里生成，所以必须起 Playwright。

## 快速上手（给人看）

告诉你的 agent：

> 去 https://github.com/BobXu2358/social-creator-collector ，读 AGENTS.md，帮我把这个采集器配好。

agent 会装依赖、引导你用 Cookie-Editor 导出 cookie、跑只读采集。

## 手动上手

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium        # 抖音那条路需要

python -m collector init --account xgame      # 建目录
# 把 cookie 放进 social/_secrets/xgame/<platform>/（见 AGENTS.md）
python -m collector bilibili probe --account xgame
python -m collector bilibili summary --account xgame --days 30
python -m collector douyin import-cookies --account xgame
python -m collector douyin worklist --account xgame --days 30
```

跨平台的 Chromium 解析见 [collector/browser.py](collector/browser.py)：默认用 Playwright 自带的 chromium，不依赖任何写死路径；要用系统 Chrome 就 `--chromium <路径>` 或设 `SCC_CHROMIUM`。

## 安全

- **只读**：绝不发布、编辑、删除、评论、关注或改账号设置。
- **不回显 cookie**：长度/数量可以说，明文值不进对话、不进日志。
- 一个账号一个 namespace，secret 全在 `social/_secrets/` 下，已被 `.gitignore` 排除。

## Cookie-Editor

导出 cookie 用这个 Chrome 扩展（导出成 **JSON**，不要 Netscape 文本格式）：
https://chromewebstore.google.com/detail/cookie-editor/hlkenndednhfkekhgcdicdfddnkalmdm
