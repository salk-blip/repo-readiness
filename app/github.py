"""Bounded, commit-pinned GitHub acquisition; never checks out or executes code."""
import asyncio
import io
import os
import re
import stat
import zipfile
from pathlib import PurePosixPath
from urllib.parse import quote, urlsplit

import httpx

from .models import Coverage

MAX_DOWNLOAD = 30 * 1024 * 1024
MAX_EXPANDED = 100 * 1024 * 1024
MAX_ENTRIES = 20_000
MAX_FILES = 5_000
MAX_FILE_BYTES = 1024 * 1024
IGNORED = {"node_modules", "vendor", ".git", ".venv", "venv", "dist", "build", "__pycache__", ".next", "coverage"}
BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".woff", ".woff2", ".ttf", ".zip", ".gz", ".exe", ".dll", ".so", ".mp4", ".mp3", ".lockb"}


class ScanError(Exception):
    """Safe user-facing message; never includes remote response bodies."""


def parse_repository(value: str) -> str:
    u = urlsplit(value.strip())
    if u.scheme != "https" or u.netloc.lower() != "github.com" or u.query or u.fragment:
        raise ValueError("请输入 https://github.com/owner/repo 格式的公开仓库首页链接。")
    match = re.fullmatch(r"/([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9_.-]{1,100})/?", u.path)
    if not match:
        raise ValueError("仅支持仓库首页，不支持分支、文件或目录页面。")
    owner, repo = match.groups()
    repo = repo.removesuffix(".git")
    if repo in {"", ".", ".."}:
        raise ValueError("仓库名称无效。")
    return f"{owner}/{repo}"


async def api_json(client: httpx.AsyncClient, path: str) -> dict:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "Repo-Readiness/1.0"}
    if os.getenv("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    for attempt in range(3):
        response = await client.get(f"https://api.github.com{path}", headers=headers)
        if response.status_code == 404:
            raise ScanError("仓库不存在、非公开或当前无权访问。")
        if response.status_code == 409:
            raise ScanError("仓库为空，暂时没有可扫描的 commit。")
        if response.status_code in {403, 429}:
            raise ScanError("GitHub 限流或拒绝访问，请稍后重试；可在服务端配置 GITHUB_TOKEN。")
        if response.status_code >= 500 and attempt < 2:
            await asyncio.sleep(2 ** attempt)
            continue
        if response.status_code != 200:
            raise ScanError("GitHub 请求失败，请稍后重试。")
        return response.json()
    raise ScanError("GitHub 暂时不可用。")


async def resolve(client: httpx.AsyncClient, repository: str) -> tuple[str, str]:
    meta = await api_json(client, f"/repos/{repository}")
    if meta.get("private"):
        raise ScanError("仅支持公开仓库。")
    branch = meta["default_branch"]
    commit = await api_json(client, f"/repos/{repository}/commits/{quote(branch, safe='')}")
    sha = commit["sha"]
    if not re.fullmatch(r"[0-9a-f]{40,64}", sha):
        raise ScanError("GitHub 返回的 commit 标识无效。")
    return branch, sha


async def download(client: httpx.AsyncClient, repository: str, sha: str) -> bytes:
    # Direct codeload endpoint: no arbitrary URL, no token sent to a second host.
    async with client.stream("GET", f"https://codeload.github.com/{repository}/zip/{sha}") as r:
        if r.status_code in {403, 429}:
            raise ScanError("源码下载被限流，请稍后重试。")
        if r.status_code != 200:
            raise ScanError("源码归档下载失败，请稍后重试。")
        content = bytearray()
        async for chunk in r.aiter_bytes():
            content.extend(chunk)
            if len(content) > MAX_DOWNLOAD:
                raise ScanError("源码压缩包超过 30 MB 上限，请选择较小的仓库。")
        return bytes(content)


def read_archive(data: bytes) -> tuple[dict[str, str], Coverage]:
    coverage = Coverage()
    files: dict[str, str] = {}

    def skip(reason: str, incomplete: bool = False):
        coverage.skipped[reason] = coverage.skipped.get(reason, 0) + 1
        if incomplete:
            coverage.complete = False

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            coverage.entries = len(entries)
            if len(entries) > MAX_ENTRIES:
                coverage.warnings.append("归档条目超过 20,000，仅处理前 20,000 项。")
                coverage.complete = False
            expanded = 0
            for item in entries[:MAX_ENTRIES]:
                path = PurePosixPath(item.filename)
                if path.is_absolute() or ".." in path.parts or "\\" in item.filename:
                    skip("不安全路径", True)
                    continue
                if len(path.parts) < 2 or item.is_dir():
                    continue
                relative = PurePosixPath(*path.parts[1:])
                name = str(relative)
                if stat.S_ISLNK(item.external_attr >> 16):
                    skip("符号链接", True)
                    continue
                expanded += item.file_size
                if expanded > MAX_EXPANDED:
                    coverage.warnings.append("累计解压大小超过 100 MB，已停止读取后续条目。")
                    coverage.complete = False
                    break
                if any(p.lower() in IGNORED for p in relative.parts[:-1]):
                    skip("依赖或构建目录")
                    continue
                if relative.suffix.lower() in BINARY_EXT or name.endswith((".min.js", ".min.css", ".map")):
                    skip("二进制或压缩资源")
                    continue
                if item.file_size > MAX_FILE_BYTES:
                    skip("单文件超过 1 MB", True)
                    continue
                if len(files) >= MAX_FILES:
                    skip("文本文件数量上限", True)
                    continue
                try:
                    raw = archive.read(item)
                    if b"\x00" in raw[:8192] and not raw.startswith((b"\xff\xfe", b"\xfe\xff")):
                        skip("二进制内容")
                        continue
                    # UTF-8 is preferred; UTF-16 scripts and GB18030 Windows text are supported.
                    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
                        text = raw.decode("utf-16")
                    else:
                        try:
                            text = raw.decode("utf-8-sig")
                        except UnicodeDecodeError:
                            text = raw.decode("gb18030")
                    files[name] = text
                    coverage.scanned_bytes += len(raw)
                except (UnicodeError, OSError, RuntimeError, zipfile.BadZipFile):
                    skip("无法读取的文本", True)
    except (zipfile.BadZipFile, OSError, ValueError):
        raise ScanError("源码归档损坏或格式不受支持。") from None
    coverage.scanned_files = len(files)
    return files, coverage
