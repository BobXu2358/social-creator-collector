# Social Creator Collector

只读的 Bilibili & 抖音创作者数据采集工具。一个 CLI 覆盖两个平台，专为 AI agent（Claude Code、Codex、Cursor 等）驱动设计，也可手动使用。

## 它能采什么

- **B站**（HTTP 数据收集；`login` 用 Playwright 二维码）：登录校验、**账号当前粉丝总数**、涨粉趋势 + 单稿数据（播放/涨粉/投币/评论/点赞）、**单稿留存曲线 + 完播/平均观看 + 关注非关注播放占比 + 封标点击率相对信号 + 3秒跳出率**（稿件分析）、粉丝来源、**动态时间线（视频/图文/转发/互动抽奖 + 转发来源 + 抽奖检测来源，WBI 签名）**、视频评论、弹幕峰值分析。注意：B站不提供曝光/点击原始数，封标点击率绝对值被平台主动混淆（每次响应乘随机因子），采集器只输出稳定的相对信号（对同类中位数倍率、百分位、星级）。
- **抖音**（采集用无头浏览器；`login` 用有头浏览器）：导入 cookie、**账号当前粉丝总数**、作品列表（含可确认的合作作品标记和当前账号角色）、**作品分析批量（平均观看/5秒完播/2秒跳出）**、**单稿分析详情（完播率 + 流量来源 + 进度曲线 + 搜索词 + 观众画像）**、账号每日净增粉丝 + 相关解释指标、**单稿粉丝增量**（接口拿不到，从投稿列表 DOM 提取）、视频评论。

两个平台的数据路径不同是有原因的：B站三个 cookie 拼成请求头就够日常采集，但 `bilibili login` 仍是二维码浏览器流程；抖音的登录态和 `a-bogus` 签名只能在真浏览器里生成，所以必须起 Playwright。

## 不懂命令？直接发给 Agent

把下面这段话复制给你的 coding agent：

> 打开 https://github.com/BobXu2358/social-creator-collector，先阅读 `AGENTS.md`，帮我在本机配置这个只读采集器。先问我要采 B站、抖音还是两个平台，并让我取一个 account 名称；优先引导我扫码登录，不要让我把 cookie 发到聊天里；最后验证登录状态并告诉我是否配置成功。

## 快速上手

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
python -m playwright install chromium  # needed for QR login and all Douyin collection
python -m collector init --account xgame
python -m collector bilibili login --account xgame
python -m collector bilibili summary --account xgame --days 30
```

> **Windows（PowerShell）**：把第一行换成 `py -3 -m venv .venv` 再 `.\.venv\Scripts\Activate.ps1`（别用系统自带的 `python`——那通常是微软商店占位符，先 `winget install Python.Python.3.12` 装真 Python，用 `py -V` 确认）。其余命令一致；`tzdata` 会随依赖自动装。只有当你手动提供 B站 凭证文件时，才可跳过 `playwright install chromium`；B站 扫码登录仍需要它。

`login` 会弹一个真浏览器窗口、平台自己出二维码、你手机扫一下就存好登录态——不用再手动导 cookie。代价是它需要桌面会话（无头服务器跑不了）。cookie 过期就再 `login` 一次。

完整命令、选项、输出字段和平台语义见 [CLI 参考](docs/CLI_REFERENCE.md)；安全规则、凭证管理和故障恢复见 [AGENTS.md](AGENTS.md)。

### 判断抖音合作作品

`douyin worklist` 可在 `items[].platform_fields` 中输出：

```json
{
  "is_collaboration": true,
  "creator_role": "collaborator"
}
```

仅在 `is_collaboration` 明确为 `true` 时按合作作品处理；字段缺失不等于
`false`。`creator_role` 为 `primary`、`collaborator` 或 `unknown`。当角色为
`collaborator` 时，不要复制其他账号的单稿详情，应改由主发布账号运行
`douyin video-detail`。

## 文档导航

| Document | Purpose |
|---|---|
| [AGENTS.md](AGENTS.md) | Safety, credentials, onboarding, and executable agent operations |
| [docs/CLI_REFERENCE.md](docs/CLI_REFERENCE.md) | Detailed commands, options, outputs, and platform semantics |
| [schemas/collector-output.schema.json](schemas/collector-output.schema.json) | Machine-readable canonical row contract |
| [MAINTAINING.md](MAINTAINING.md) | Core boundaries, schema versioning, releases, and PR checks |
| [skills/social-creator-data/SKILL.md](skills/social-creator-data/SKILL.md) | Creator-metrics collection workflow |
| [skills/feedback-analytics/SKILL.md](skills/feedback-analytics/SKILL.md) | Comment and danmaku analysis workflow |
