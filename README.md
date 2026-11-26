# Repo Ready · Git 仓库接入体检

输入一个公开 GitHub 仓库首页链接，查看文档、依赖与启动、可移植性、敏感信息四个维度的静态体检报告。每条发现带规则、严重度、置信度、文件位置及修复建议，源码链接固定到扫描 commit。

面向「Git 仓库接入体检 · 48 小时实战题」实现。核心扫描无需 AI 密钥，无需安装或执行目标项目。

![真实仓库体检报告（按文档类别筛选）](docs/report-desktop.jpg)

[移动端截图](docs/report-mobile.jpg)

## 快速启动

需要 Python 3.12+，以及到 `api.github.com` 和 `codeload.github.com` 的 HTTPS 连接。

```bash
git clone https://github.com/salk-blip/repo-readiness.git
cd repo-readiness
python -m venv .venv
```

Windows PowerShell：

```powershell
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

macOS / Linux：

```bash
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000>。输入 `https://github.com/pallets/flask` 或你的公开仓库首页；也接受结尾 `.git`。首次扫描会下载固定 commit 的源码，耗时取决于网络和仓库大小。

GitHub 匿名 REST 请求通常每小时仅 60 次，演示前建议设置只用于公开仓库读取的服务端 Token，然后启动服务：

```powershell
$env:GITHUB_TOKEN = "<你的 GitHub Token>"
```

Linux / macOS 使用 `export GITHUB_TOKEN="<你的 GitHub Token>"`。不要提交真实 Token。`.env.example` 只是配置参考，程序不会自动加载 `.env`。

Docker：

```bash
docker build -t repo-readiness .
docker run --rm -p 8000:8000 -e GITHUB_TOKEN repo-readiness
```

默认报告保存在 `data/reports.sqlite3`；可通过 `DATABASE_PATH` 指定路径，容器需挂载可写卷才能在删除容器后保留报告。使用**一个 Uvicorn worker**，后台任务和并发限制均在进程内。最多同时扫描两个仓库，最多容纳 12 个未完成任务。

## 功能与实现

```text
GitHub 首页 URL → 元数据 / 默认分支 → commit SHA
  → 下载该 SHA 的 ZIP → 有界读取文本 → 规则检测 / 脱敏
  → 四维评分 → SQLite 脱敏报告 → 页面 / JSON / 可选 AI 建议
```

- **README**：检查存在、项目说明、环境要求、安装、启动、配置六项信号，支持中英文线索。
- **依赖与启动**：深入识别 Python / Node.js 清单、锁文件、固定版本 requirements、package scripts、Python 入口与 Docker 配置。其他生态显示“不适用”，按适用维度折算。
- **Windows 脚本**：列出 `.bat` / `.cmd` / `.ps1` / `.vbs`；同目录同名 `.sh` 或 README 跨平台命令视为替代线索，仍需人工确认用途等价。
- **硬编码路径**：个人盘符路径、UNC、`/Users/`、`/home/`；代码字符串里的 `/opt/`、`/srv/` 等绝对路径另作提示。URL、API 路由、`/usr/bin/env` 不直接扣分。
- **敏感信息**：有值的 `sk-`、`AKID`、Bearer、私钥标记、敏感变量赋值、已入库 `.env`。占位符和环境变量引用不按具体凭证扣分；文档、示例和测试中的凭证模式降为低风险，私钥标记保留高风险。
- **报告页面**：四维条形图、风险类别筛选、折叠证据、固定 commit 源码跳转、JSON 下载；刷新带 `?scan=任务ID` 的页面可恢复查看报告。

前端采用原生 HTML/CSS/JS 条形图，无 npm 构建步骤和第三方 CDN 依赖。FastAPI 自动提供 `/openapi.json` 接口描述。

### 评分规则（v1.0.0）

| 维度 | 规则 |
|---|---|
| 文档 25 分 | README 存在 5 分，其余五项各 4 分 |
| 依赖与启动 25 分 | 清单 10、锁定版本机制 5、静态入口 10 |
| 可移植性 20 分 | 个人绝对路径每行扣 2、最多扣 10；未识别替代方式的 Windows 脚本每个扣 2、最多扣 10 |
| 敏感信息 30 分 | 高/中/低风险各扣 15/5/1；同一文件行号按最高项去重，维度最低 0 |

总分按适用维度折算为百分制，使用 Python `round` 取整。85–100 为“较易接入”，65–84 为“建议修复后接入”，更低为“接入阻碍较多”。高置信度高风险凭证独立触发醒目告警，不被总分掩盖。扫描不完整时仅显示暂定分数。

**这不是项目质量排名，也不证明代码能运行。** 文档启发式会漏掉外链说明，测试样例可能拉低敏感维度，单仓库多项目当前按仓库整体聚合，不证明每个子项目都具备启动资料。所有疑似泄露都需要核实，工具不调用目标凭证验证有效性。

### 扫描边界

只扫描默认分支的当前 commit，不扫描 Git 历史、子模块、LFS 实体。二进制、依赖目录和构建资源为有意排除范围，不据此称为扫描失败；页面列出排除数量。

| 限制 | 行为 |
|---|---|
| 下载 ZIP 30 MB | 超限失败，提示选择较小仓库 |
| 归档条目 20,000 / 累计解压 100 MB | 截断并标记不完整 |
| 文本文件 5,000 / 单文件 1 MB | 跳过并标记不完整 |
| 规则发现 2,000 | 停止后续检测并标记不完整 |
| 扫描任务 120 秒 | 超时失败，可重新提交 |

ZIP 仅在内存中读取，不解压到磁盘；拒绝危险路径和符号链接。支持 UTF-8、带 BOM 的 UTF-16 和 GB18030 文本。后台文本线程超时后不能被 Python 强制中断，因此依赖上述体积与数量限制约束资源；生产环境可进一步使用独立受限工作进程。

SQLite 只保存脱敏报告，不保存原始源码或归档。敏感发现隐藏整个证据行；其他发现的证据也先进行凭证模式替换，私钥块正文不输出。未识别的敏感信息仍可能遗漏，查看 JSON 前应理解这是启发式工具。日志不记录源码、Token 或上游错误正文。

## API 与可选 AI

| 接口 | 行为 |
|---|---|
| `POST /api/scans` | 请求 `{"repository_url":"https://github.com/owner/repo"}`，202 返回 `id` |
| `GET /api/scans/{id}` | 状态、阶段、错误和完成后的报告 |
| `GET /api/scans/{id}/report` | 下载脱敏 JSON |
| `POST /api/scans/{id}/advice` | 生成并缓存可选 AI 优先级建议 |
| `GET /health` | 服务健康检查 |

状态：`queued` → `running` → `complete` / `partial` / `failed`。重启后未完成任务标记失败，可重新提交。无效 URL 返回 422，队列满返回 429，报告未生成返回 409。

可选配置：`AI_API_KEY`、`AI_MODEL`（默认 `gpt-4.1-mini`）、`AI_BASE_URL`（默认 `https://api.openai.com/v1`）。兼容 Chat Completions JSON 输出接口。

AI 只接收前 30 条发现的 ID、类别、严重度、固定规则标题和建议，不发送源码、文件路径或证据。模型选择最多三项处理优先级，建议文本限制在四个固定动作中；未知 ID、自由文本或无效 JSON 被拒绝。**AI 不计算、不更改规则评分。** 未配置密钥、模型失败或超时不影响规则报告。真实模型付费调用未作为核心验收前置条件，响应校验和失败降级通过 MockTransport 测试。

## 更新跟踪设计（未实现）

1. **发现更新**：调度器定期查询默认分支 SHA，使用 ETag 条件请求；用户也可手动刷新。只有目标仓库有管理授权时才配置 webhook，不能假设可以给任意公开仓库安装 webhook。
2. **避免全量重算**：保存上一版本 `path → blob SHA` 清单，以 `blob SHA + 规则版本 + 规则配置` 缓存内容检测结果。新增和修改文件重新扫描；删除文件移除发现；重命名文件重跑路径相关规则并更新源码链接。
3. **重新聚合**：文档、清单、启动入口、脚本对应关系变化后，重新计算仓库级发现与总分。规则版本升级使相关缓存失效。README 等上下文改变时也重算受影响的上下文规则，不能只按文件内容缓存最终结论。
4. **commit 溯源**：报告保存 SHA、分支、扫描时间、规则版本、覆盖范围。下载、文件清单与源码链接都固定到同一 SHA，避免扫描中途分支更新导致混合版本。
5. **失败和限流**：保留上一次成功报告并显示“更新失败，当前为旧 commit”；按 `Retry-After` / `X-RateLimit-Reset` 调度重试，指数退避且设置上限。新报告完整写入后再原子切换当前指针，失败结果不覆盖旧报告。

第一版每次提交会重新扫描；没有后台更新调度器、增量缓存或自动 webhook。当前网络读取超时 20 秒，元数据 5xx 最多尝试三次；403/429 立即返回可操作提示，避免无意义重试。

参考：[GitHub 限流](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)、[条件请求与退避](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api)。

## 验证与演示

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

测试包括完整项目精确评分、合成风险精确扣分、凭证占位符、私钥正文隔离、URL/包名误报、UTF-16 脚本、归档截断、队列 API 流程、网络失败、重启恢复，以及模型输出校验。所有样本凭证均为合成数据。

2026-11-26 验证记录：独立 Python 3.12 虚拟环境中 **36 项测试通过**；`node --check app/static/app.js` 通过；真实扫描 Flask 的 231 个文本文件，成功生成并导出报告；浏览器已验证筛选、展开证据和无 AI 密钥降级，桌面与窄屏布局无横向溢出，未捕获到页面控制台错误。测试依赖中有 Starlette 对 httpx TestClient 的弃用提示，目前不影响测试通过。

真实集成示例见 [`docs/example-report.json`](docs/example-report.json)，记录了被扫描的 Flask commit 和扫描覆盖范围。结果仅表示本工具的规则输出，不能理解为 Flask 项目存在已确认的泄露。

AI 使用方式、真实错误与修正过程见 [`AI_USAGE.md`](AI_USAGE.md)。部署到公共网络前需要补充访客限流、任务隔离和数据保留策略；当前适合本地或受控环境演示。Docker 配置已提供，未在本机执行 Docker 构建。
