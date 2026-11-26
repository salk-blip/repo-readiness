import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from . import github
from .advice import generate_advice
from .scanner import scan
from .store import Store

ROOT = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app):
    app.state.store = Store(os.getenv("DATABASE_PATH", str(ROOT.parent / "data" / "reports.sqlite3")))
    app.state.semaphore = asyncio.Semaphore(2)
    app.state.tasks = set()
    app.state.advice_tasks = set()
    yield
    for task in app.state.tasks:
        task.cancel()
    await asyncio.gather(*app.state.tasks, return_exceptions=True)


app = FastAPI(title="Repo Ready · Git 仓库接入体检", lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
templates = Jinja2Templates(directory=ROOT / "templates")


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
    return response


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/health")
async def health():
    return {"status": "ok"}


class ScanInput(BaseModel):
    repository_url: str = Field(min_length=1, max_length=300)


async def worker(scan_id: str):
    store = app.state.store
    async with app.state.semaphore:
        record = store.get(scan_id)
        try:
            async with asyncio.timeout(120):
                record.update(status="running", stage="解析默认分支与 commit")
                store.put(record)
                async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
                    branch, sha = await github.resolve(client, record["repository"])
                    record.update(stage="下载固定 commit 的源码", branch=branch, commit_sha=sha)
                    store.put(record)
                    data = await github.download(client, record["repository"], sha)
                record["stage"] = "扫描文本、脱敏与评分"
                store.put(record)
                files, coverage = await asyncio.to_thread(github.read_archive, data)
                report = await asyncio.to_thread(scan, files, record["repository"], branch, sha, coverage)
                record.update(status="complete" if coverage.complete else "partial", stage="报告已生成", report=report.model_dump())
                store.put(record)
        except TimeoutError:
            record.update(status="failed", stage="扫描超时", error="超过 120 秒时限，请重试或选择较小仓库。")
            store.put(record)
        except (github.ScanError, httpx.HTTPError) as exc:
            message = str(exc) if isinstance(exc, github.ScanError) else "连接 GitHub 失败，请检查网络后重试。"
            record.update(status="failed", stage="扫描失败", error=message)
            store.put(record)
        except Exception:
            # Do not log exception values: untrusted source or response bodies may contain credentials.
            record.update(status="failed", stage="扫描失败", error="无法完成该仓库的静态分析，请重试或检查仓库格式。")
            store.put(record)


@app.post("/api/scans", status_code=202)
async def create_scan(body: ScanInput):
    try:
        repository = github.parse_repository(body.repository_url)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if len(app.state.tasks) >= 12:
        raise HTTPException(429, "当前队列已满，请稍后重试。")
    scan_id = uuid.uuid4().hex
    app.state.store.put({"id": scan_id, "repository": repository, "status": "queued", "stage": "排队等待扫描",
        "created_at": datetime.now(timezone.utc).isoformat(), "report": None, "error": None})
    task = asyncio.create_task(worker(scan_id))
    app.state.tasks.add(task)
    task.add_done_callback(app.state.tasks.discard)
    return {"id": scan_id, "status": "queued"}


def get_record(scan_id):
    record = app.state.store.get(scan_id)
    if not record:
        raise HTTPException(404, "扫描记录不存在。")
    return record


@app.get("/api/scans/{scan_id}")
async def get_scan(scan_id: str):
    return get_record(scan_id)


@app.get("/api/scans/{scan_id}/report")
async def download_report(scan_id: str):
    record = get_record(scan_id)
    if not record["report"]:
        raise HTTPException(409, "报告尚未生成。")
    return JSONResponse(record["report"], headers={"Content-Disposition": 'attachment; filename="repo-readiness-report.json"'})


@app.post("/api/scans/{scan_id}/advice")
async def advice(scan_id: str):
    record = get_record(scan_id)
    if not record["report"]:
        raise HTTPException(409, "请先完成扫描。")
    if record.get("advice"):
        return record["advice"]
    if scan_id in app.state.advice_tasks:
        raise HTTPException(409, "建议正在生成，请稍后查看。")
    app.state.advice_tasks.add(scan_id)
    try:
        result = await generate_advice(record["report"])
        if result["available"]:
            record["advice"] = result
            app.state.store.put(record)
        return result
    except github.ScanError as exc:
        raise HTTPException(503, str(exc)) from None
    finally:
        app.state.advice_tasks.discard(scan_id)
