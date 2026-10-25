# Instagram VOC 评论采集器

## 一眼看懂

- 这是什么：按商品编号找 Instagram 帖子并采公开评论。
- 什么时候用：想看看图文和短视频底下怎么聊这款商品时。
- 给它什么：商品编号，和一个已登录的 Chrome 窗口。
- 它给你什么：评论明细和五张表 Excel，含媒体与质量记录。

## 项目简介

`instagram-voc-collector` 是由 Amazon ASIN 驱动的 Instagram Post/Reel 公开评论采集工具和 Agent Skill。正式流程从可见 Amazon 商品页面构建商品画像，发现并筛选相关媒体，再通过已登录 Chrome 的 CDP（Chrome 调试协议）会话读取实际渲染的评论和回复，输出带审计信息的 JSONL、CSV 与 Excel。

默认运行路径是 Python 编排、Node.js Playwright 和 Chrome CDP（Chrome 调试协议），仓库中的扩展保留为显式选择的兼容方式。仓库不调用 Instagram 官方 API、不导出登录凭据，也不提供 LLM（大模型）语义判断或托管服务；采集结果只代表当前可见 DOM（页面结构）的范围，不等于平台全部评论。

## 核心功能

- 🧭 **输入解析**：单 ASIN、多 ASIN 或文件
- 🔍 **媒体发现**：按 shortcode 去重并留存源
- ⚖️ **相关性分级**：规则评分分 HIGH 与 LOW
- 🧵 **评论展开**：串行展开回复并滚动容器
- 🧬 **技术去重**：按稳定评论 ID 保留父子
- 💾 **断点续采**：checkpoint 与限流冷却

## 技术栈

| 层级 | 技术 | 版本 |
| --- | --- | --- |
| 编排与导出 | Python 标准库 | >=3.10 |
| 浏览器控制 | Node.js + playwright-core | ^1.63.0 |
| Chrome 启动 | Windows PowerShell + CDP | — |
| 兼容采集 | Chrome 扩展 + Python localhost bridge | — |
| Excel 写入 | 自带 `xlsx_writer.py`（标准库） | — |
| 测试 | Python unittest / Node test / GitHub Actions | — |

版本取自 `pyproject.toml`、`.python-version`、`package.json` 与 `package-lock.json`；Node.js 的 20+ 只是 CI 与文档要求，`package.json` 没有 engines 字段，故记「—」。`requirements.txt` 当前仅含说明，没有第三方 Python 依赖。

## 快速开始

需要 Python 3.10+、Node.js 20+ 和已安装的 Google Chrome；采集需要能人工登录 Instagram 的 Chrome 会话。以下为 Windows PowerShell 写法。

1. 克隆仓库并进入目录

   ```powershell
   git clone https://github.com/fubo-ops/instagram-voc-collector.git
   cd instagram-voc-collector
   ```

2. 创建虚拟环境、安装包和 Node 依赖，并确认入口可用

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install .
   npm ci
   .\.venv\Scripts\python.exe scripts/instagram_automation.py --help
   ```

3. 启动专用 Chrome 会话，在窗口里人工登录 Instagram

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\start_instagram_cdp.ps1
   ```

4. 按 ASIN 采集，输出到 `outputs/` 下的指定目录

   ```powershell
   .\.venv\Scripts\python.exe scripts/instagram_automation.py collect --asin ASIN --transport cdp --target-posts 30 --target-comments 1000 --out-dir outputs/run-001
   ```

5. 多 ASIN 或断点续跑时复用同一个输出目录

   ```powershell
   .\.venv\Scripts\python.exe scripts/instagram_automation.py collect --asins ASIN1,ASIN2 --transport cdp --out-dir outputs/run-001
   ```

6. 跑离线测试与语法检查，不需要浏览器登录态

   ```bash
   python -B -m unittest discover -s tests -v
   npm test
   npm run check
   ```

第 3 步的启动脚本固定用 `127.0.0.1:9333` 和 `outputs/instagram-cdp-profile`，首次登录由人工完成。采集时默认导航等待 20–35 秒、评论操作等待 3–6 秒，每 5 个帖子休息 2–5 分钟；限流冷却级别依次为 1、4、12 小时。

## 项目结构

只列两层，完整文件清单见文末《详细说明》。

```text
instagram-voc-collector/
├── .github/                                # CI 工作流目录
├── .gitignore                              # 忽略 Profile、运行数据与产物
├── .python-version                         # 本地 Python 版本锁定
├── LICENSE                                 # MIT 许可证
├── README.md                               # 项目总览与快速开始
├── SKILL.md                                # Agent 入口与交付约定
├── agents/                                 # Agent 展示配置
├── extension/                              # 兼容用 Chrome 扩展
├── package.json                            # Node 依赖、测试与检查脚本
├── package-lock.json                       # Node 依赖锁定
├── pyproject.toml                          # Python 包元数据
├── references/                             # 采集指南与字段契约
│   ├── collection-guide.md                 # 采集、限流与恢复指南
│   └── raw-record-schema.md                # 原始记录与输出字段契约
├── requirements.txt                        # 仅说明，无第三方依赖
├── scripts/                                # 采集编排与导出脚本
│   ├── instagram_automation.py             # 正式采集编排入口
│   ├── instagram_collector.py              # 规范化与技术去重
│   ├── instagram_discovery.py              # 查询计划与媒体发现
│   ├── instagram_opencli.py                # 单帖调试辅助
│   ├── instagram_playwright_collector.cjs  # Node Playwright 采集实现
│   ├── start_instagram_cdp.ps1             # Windows Chrome CDP 启动器
│   └── xlsx_writer.py                      # 标准库生成五表 Excel
└── tests/                                  # 离线测试
    ├── test_automation_extension.cjs       # 扩展与自动化协作测试
    ├── test_capture_core.cjs               # 页面采集核心测试
    ├── test_instagram_automation.py        # 编排入口测试
    ├── test_instagram_cdp.py               # CDP 连接与预检测试
    ├── test_instagram_collector.py         # 采集与去重测试
    ├── test_instagram_discovery.py         # 发现与查询计划测试
    ├── test_instagram_opencli.py           # 单帖调试入口测试
    ├── test_instagram_playwright.cjs       # Playwright 采集测试
    └── test_repository_distribution.py     # 仓库分发结构测试
```

## 架构图

```text
┌──────────────────────────────────────────────┐
│   单个 / 多个 Amazon ASIN                    │
└───────────────────────┬──────────────────────┘
                        ▼
┌──────────────────────────────────────────────┐
│   instagram_automation.py 编排               │
└───────────────────────┬──────────────────────┘
                        ▼
┌──────────────────────────────────────────────┐
│   可见商品信息 + 查询计划（discovery）       │
└───────────────────────┬──────────────────────┘
                        ▼
┌──────────────────────────────────────────────┐
│   候选媒体发现、shortcode 去重与规则评分     │
└───────────────────────┬──────────────────────┘
                        ▼
┌──────────────────────────────────────────────┐
│   Node Playwright + 专用 Chrome CDP 标签页   │
└───────────────────────┬──────────────────────┘
                        ▼
┌──────────────────────────────────────────────┐
│   页面身份核对、评论展开与滚动               │
└───────────────────────┬──────────────────────┘
                        ▼
┌──────────────────────────────────────────────┐
│   checkpoint / 媒体审计 / 证据与 JSONL、CSV  │
└───────────────────────┬──────────────────────┘
                        ▼
┌──────────────────────────────────────────────┐
│   xlsx_writer.py → 固定五表 Excel            │
└──────────────────────────────────────────────┘
```

身份核对会比对页面 URL、canonical、`og:url` 与评论链接的媒体身份；限流时保存 checkpoint 并停止整轮。Profile、评论数据、checkpoint 和登录资料都不进 Git。

## 开发计划

### Phase 1: 输入与发现

- ✅ 单 ASIN、多 ASIN 与 ASIN 文件输入
- ✅ 商品画像与分组查询计划
- ✅ Post/Reel 发现、shortcode 去重与来源保留

### Phase 2: 采集与身份核对

- ✅ 规则评分分级（HIGH / MEDIUM / LOW）
- ✅ CDP（Chrome 调试协议）会话内评论与回复串行展开
- ✅ 页面 URL、canonical、`og:url` 身份核对

### Phase 3: 输出与恢复

- ✅ 稳定 ID 技术去重与父子关系保留
- ✅ checkpoint 断点续采与限流冷却
- ✅ 固定五表 Excel 与媒体审计记录

### Phase 4: 工程化与上线

- ✅ 离线测试与跨平台 GitHub Actions CI
- ✅ Excel 由标准库生成，无第三方运行依赖
- ⬜ 最新登录态浏览器的采集验收记录
- ⬜ 其他操作系统的真实采集验收
- ⬜ 依赖本机 Windows 路径的测试用例隔离
- ⬜ 独立开发路线图与排期

## 文档

- [Skill 入口](SKILL.md)与 [展示配置](agents/openai.yaml)
- [采集、限流与恢复指南](references/collection-guide.md)
- [原始记录与输出字段](references/raw-record-schema.md)
- [Windows Chrome CDP 启动脚本](scripts/start_instagram_cdp.ps1)
- [Node 依赖与命令](package.json)、[Python 包配置](pyproject.toml)
- [CI 配置](.github/workflows/test.yml)

## 许可证

代码采用 [MIT License](LICENSE)。评论内容与外部平台的授权范围不由本仓库代码许可证授予。

---

原 README 里的环境要求、安装配置、完整命令、数据边界、项目文件清单、开发状态与历史记录等细节，已全部迁移到 [docs/详细说明.md](docs/详细说明.md)。
