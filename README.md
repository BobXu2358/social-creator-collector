# Social Creator Collector

只读的 Bilibili & 抖音创作者数据采集工具。一个 CLI 覆盖两个平台，专为 AI agent（Claude Code、Codex、Cursor 等）驱动设计，也可手动使用。

## 它能采什么

- **B站**（纯 HTTP）：登录校验、**账号当前粉丝总数**、涨粉趋势 + 单稿数据（播放/涨粉/投币/评论/点赞）、**单稿留存曲线 + 完播/平均观看 + 关注非关注播放占比 + 封标点击率相对信号 + 3秒跳出率**（稿件分析）、粉丝来源、视频评论、弹幕峰值分析。注意：B站不提供曝光/点击原始数，封标点击率绝对值被平台主动混淆（每次响应乘随机因子），采集器只输出稳定的相对信号（对同类中位数倍率、百分位、星级）。
- **抖音**（无头浏览器）：导入 cookie、**账号当前粉丝总数**、作品列表、**作品分析批量（平均观看/5秒完播/2秒跳出）**、**单稿分析详情（完播率 + 流量来源 + 进度曲线 + 搜索词 + 观众画像）**、账号每日净增粉丝 + 相关解释指标、**单稿粉丝增量**（接口拿不到，从投稿列表 DOM 提取）、视频评论。

两个平台的数据路径不同是有原因的：B站三个 cookie 拼成请求头就够了，抖音的登录态和 `a-bogus` 签名只能在真浏览器里生成，所以必须起 Playwright。

## 快速上手（给人看）

告诉你的 agent：

> 去 https://github.com/BobXu2358/social-creator-collector ，读 AGENTS.md，帮我把这个采集器配好。

agent 会装依赖、引导你用 Cookie-Editor 导出 cookie、跑只读采集。

## 手动上手

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium          # 扫码登录和抖音采集都需要

python -m collector init             --account xgame   # 建目录
python -m collector bilibili login   --account xgame   # 弹窗扫码 → 存凭证
python -m collector bilibili summary --account xgame --days 30
python -m collector bilibili video-detail --account xgame --bvid BVxxx   # 单稿留存/完播/观众/封标点击率信号/3秒跳出
python -m collector bilibili fan-source --account xgame
python -m collector douyin   login   --account xgame   # 弹窗扫码 → 存 session
python -m collector douyin   worklist --account xgame --days 30
python -m collector douyin   item-analysis --account xgame --days 30     # 批量：平均观看/5s完播/2s跳出
python -m collector douyin   video-detail --account xgame --aweme-id ID  # 单稿：完播率/流量来源/进度/搜索词
python -m collector douyin   fan-trend --account xgame --days 30
```

> **Windows（PowerShell）**：把第一行换成 `py -3 -m venv .venv` 再 `.\.venv\Scripts\Activate.ps1`（别用系统自带的 `python`——那通常是微软商店占位符，先 `winget install Python.Python.3.12` 装真 Python，用 `py -V` 确认）。其余命令一致；`tzdata` 会随依赖自动装。只用 B站 可跳过 `playwright install chromium`（纯 HTTP，不开浏览器）。

`login` 会弹一个真浏览器窗口、平台自己出二维码、你手机扫一下就存好登录态——不用再手动导 cookie。代价是它需要桌面会话（无头服务器跑不了）。cookie 过期就再 `login` 一次。

## 装成命令（给同事复用）

```bash
pip install -e .          # 或 pip install git+https://github.com/BobXu2358/social-creator-collector
collector --version
```

装好后直接敲 `collector <平台> <动作> --account <你的账号>`（和 `python -m collector ...` 等价）。同事各自装一份、各跑各的账号即可——`--account` 是命名空间，凭证按账号隔离在 `social/_secrets/<账号>/` 下，互不串。

跨平台的 Chromium 解析见 [collector/browser.py](collector/browser.py)：默认用 Playwright 自带的 chromium，不依赖任何写死路径；要用系统 Chrome 就 `--chromium <路径>` 或设 `SCC_CHROMIUM`。

## 安全

- **只读**：绝不发布、编辑、删除、评论、关注或改账号设置。
- **不回显 cookie**：长度/数量可以说，明文值不进对话、不进日志。
- 一个账号一个 namespace，secret 全在 `social/_secrets/` 下，已被 `.gitignore` 排除。
- 如果现有命令不够回答业务问题，可以让 agent 做一次性的只读 discovery；但不要改本地 core 代码。
  发现稳定有用的新数据时，用 GitHub 的 Discovery finding 模板开 issue，由维护者决定是否加入正式命令。

## Cookie-Editor（兜底）

扫码登录是主路。只有在扫码被风控拒、或要在无头机器上跑时，才退回手动导 cookie：用这个
Chrome 扩展导出成 **JSON**（不要 Netscape 文本格式），存进 `social/_secrets/` 后用
`douyin import-cookies` 导入。详见 AGENTS.md。
https://chromewebstore.google.com/detail/cookie-editor/hlkenndednhfkekhgcdicdfddnkalmdm
