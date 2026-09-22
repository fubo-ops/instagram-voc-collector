# Instagram VOC Collector

一个可独立运行、也可作为 Codex Skill 使用的 Instagram Post/Reel VOC 采集器。正式流程只输入 Amazon ASIN，自动构建商品画像、生成检索词、发现并筛选相关媒体，然后通过已登录的 Chrome CDP 会话采集页面实际渲染的评论和回复。

不调用 Instagram API，不复制或导出 Cookie、Token 等登录凭证。仓库中的 Chrome 扩展仅作为兼容备用；正式默认运行模式为 Playwright + CDP。

## 支持环境

- Windows 10/11
- Python 3.10–3.13
- Node.js 20+
- Google Chrome

GitHub Actions 会在 Windows、macOS、Linux及 Python 3.10/3.13 上执行离线单元测试；真实采集需要 Windows Chrome 和用户自行登录的 Instagram 会话。

## 安装

```powershell
git clone https://github.com/fubo-ops/instagram-voc-collector.git
cd instagram-voc-collector
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
npm ci
```

该流程连接系统 Chrome，因此不需要执行 `playwright install chromium`。

## 首次登录

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_instagram_cdp.ps1
```

脚本使用固定 CDP 端口 `9333` 和仓库下的 `outputs/instagram-cdp-profile`。首次在打开的专用 Chrome 中登录 Instagram；后续任务复用此配置。不要将 `outputs/` 上传到 Git。

## ASIN 批量采集

```powershell
python scripts\instagram_automation.py collect `
  --asin B003ULL1NQ `
  --transport cdp `
  --target-posts 30 `
  --target-comments 1000 `
  --out-dir outputs\run-001
```

多个 ASIN：

```powershell
python scripts\instagram_automation.py collect `
  --asins B003ULL1NQ,B0XXXXXXXX `
  --out-dir outputs\run-002
```

正式流程会：

1. 从实际渲染的 Amazon 页面构建商品语义画像和查询计划；
2. 发现多个 Instagram Post/Reel，按 shortcode 去重并标记 HIGH/MEDIUM/LOW；
3. 仅采集通过语义门禁的媒体，验证最终 URL、shortcode、canonical 和 `og:url`；
4. 串行滚动评论容器、展开可见回复，并持续写入 checkpoint；
5. 保存原始父子关系，仅做稳定 ID 或严格字段组合的技术去重。

## 访问频率与断点续采

- Instagram 页面导航随机等待 20–35 秒；评论与回复操作随机等待 3–6 秒。
- 每完成 5 个帖子随机休息 2–5 分钟，全部浏览器任务严格串行。
- 发现 HTTP 429、Try again later 或操作频繁提示后立即保存 checkpoint 并停止。
- 分级冷却为 1 小时、4 小时、12 小时；冷却期不会启动浏览器规避限制。
- 使用同一 `--out-dir` 再次运行即可从 `automation_checkpoint.json` 续跑。

## 调试模式

直接 URL 只用于单帖 smoke 测试，不是正式入口：

```powershell
python scripts\instagram_automation.py smoke `
  --transport cdp `
  --smoke-url "https://www.instagram.com/p/POST_CODE/" `
  --target-posts 1 `
  --out-dir outputs\smoke
```

## 输出

每个运行目录包含原始 JSONL/CSV、checkpoint、manifest、候选与媒体审计文件，以及 `instagram_raw_comments.xlsx`。Excel 固定包含：

- `Raw_Comments`
- `Media_Audit`
- `Query_Plan`
- `Run_Summary`
- `Quality_Gate`

页面显示的评论总数只进入审计字段，不会被当作实际采集数。`comment_id` 按文本保存，回复保留 `parent_comment_id`。

## 验证

```powershell
python -m unittest discover -s tests -v
npm test
npm run check
```

详细运行规则见 [`references/collection-guide.md`](references/collection-guide.md)，字段说明见 [`references/raw-record-schema.md`](references/raw-record-schema.md)。

## 数据与凭证

- `.gitignore` 排除输出、Excel、checkpoint、浏览器 Profile、缓存及日志。
- 仓库不包含评论结果、历史候选 URL、Cookie、Token 或账号凭证。
- CDP 仅读取浏览器实际渲染内容，不调用 Instagram 隐藏接口。
