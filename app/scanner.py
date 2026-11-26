"""Deterministic static heuristics. Never imports target project modules."""
import hashlib
import json
import re
import tomllib
from datetime import datetime, timezone
from pathlib import PurePosixPath
from urllib.parse import quote

from .models import Coverage, Dimension, Finding, Report

TOKEN = re.compile(r"(?i)(?<![\w-])(?:sk-[a-z0-9_-]{8,}|AKID[a-z0-9]{8,}|Bearer\s+[a-z0-9._~+/=-]{8,})")
ASSIGNMENT = re.compile(r'''(?i)(["']?(?:[\w-]*(?:api[_-]?key|secret(?:[_-]?key)?|password|passwd|access[_-]?token|auth[_-]?token|private[_-]?key))["']?\s*[:=]\s*)([^\r\n,;}]+)''')
PRIVATE = re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----")
USER_PATH = re.compile(r'''(?i)(?<![\w])(?:[a-z]:[\\/](?!/)[^\s"'<>`]*|\\\\[a-z0-9_.-]+\\[^\s"'<>`]*|/(?:Users|home)/[^\s"'<>`]+)''')
ABSOLUTE = re.compile(r'''["'](/(?:opt|srv|var|mnt|data|tmp|etc)/[^"'\r\n]+)["']''')
PLACEHOLDER = re.compile(r"(?i)(example|placeholder|your[_ -]|dummy|changeme|replace|redacted|\$\{|\$[A-Z_]+|<[^>]+>|\*{3,}|x{4,})")
LEVEL = {"high": 0, "medium": 1, "low": 2, "info": 3}


def placeholder(value: str) -> bool:
    return bool(PLACEHOLDER.search(value)) or bool(re.search(r"(?i)(os\.(?:getenv|environ)|process\.env|environ\[|getenv\(|get_secret\()", value)) or value.strip(" \t\"'") in {"", "None", "null", "false"}


def redact(text: str) -> str:
    """Only redacted evidence crosses the scanner boundary."""
    text = TOKEN.sub("[REDACTED_TOKEN]", text)
    text = ASSIGNMENT.sub(lambda m: m[1] + "[REDACTED_VALUE]", text)
    text = re.sub(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*", "[REDACTED_PRIVATE_KEY]", text)
    return text[:240]


def literal_credential(match, extension: str) -> bool:
    if not match or placeholder(match[2]):
        return False
    value = match[2].strip()
    if re.match(r'''^[rub]*["']''', value, re.I):
        return len(value.strip("\"'")) >= 4
    if extension in {".py", ".js", ".ts", ".tsx", ".jsx"}:
        return False  # Type annotations, function calls and variable references are not literal secrets.
    return bool(re.fullmatch(r"[A-Za-z0-9_./+=@-]{4,}", value))


def scan(files: dict[str, str], repository: str, branch: str, sha: str, coverage: Coverage) -> Report:
    findings: list[Finding] = []

    def add(rule, category, severity, confidence, title, explanation, suggestion, path=None, line=None, evidence=""):
        stable = hashlib.sha256(f"{rule}:{path}:{line}".encode()).hexdigest()[:12]
        # Secret findings deliberately carry no original source line at all: even a
        # generic PRIVATE_KEY assignment could contain an unrecognized credential.
        safe_evidence = "[敏感证据已隐藏，请在源码中核实]" if category == "secrets" else redact(evidence)
        findings.append(Finding(id=stable, rule_id=rule, category=category, severity=severity,
            confidence=confidence, path=path, line=line, title=title, evidence=safe_evidence,
            explanation=explanation, suggestion=suggestion,
            source_url=f"https://github.com/{repository}/blob/{sha}/{quote(path, safe='/')}#L{line or 1}" if path else None))

    readmes = [p for p in files if PurePosixPath(p).name.lower() in {"readme", "readme.md", "readme.rst", "readme.txt"}]
    readme_path = min(readmes, key=lambda p: (p.count("/"), len(p))) if readmes else None
    readme = files[readme_path] if readme_path else ""
    doc_checks = [
        ("README 存在", bool(readme_path), 5),
        ("项目说明", bool(re.search(r"(?im)(description|overview|介绍|简介|概述|说明)|^[^#`\s].{24,}", readme)), 4),
        ("环境要求", bool(re.search(r"(?i)(prerequisite|requirements|environment|环境|前置|python\s*\d|node\s*[>=v]*\d)", readme)), 4),
        ("安装步骤", bool(re.search(r"(?i)(installation|install|安装|pip\s+install|npm\s+(?:ci|install))", readme)), 4),
        ("启动步骤", bool(re.search(r"(?i)(quick\s*start|getting\s*started|启动|运行|npm\s+(?:run|start)|uvicorn|python\s+\S+\.py)", readme)), 4),
        ("配置说明", bool(re.search(r"(?i)(configuration|configure|配置|environment\s+variables|环境变量|\.env)", readme)), 4),
    ]
    doc = Dimension(key="documentation", label="文档完整度", score=sum(points for _, ok, points in doc_checks if ok), maximum=25,
        details=[f"{'已识别' if ok else '未识别'}：{label}（{points} 分）" for label, ok, points in doc_checks])
    for label, ok, _ in doc_checks:
        if not ok:
            add("DOC_" + str(len(findings)), "documentation", "low", "medium", f"未识别到{label}",
                "依据静态文档信号判断，可能需要人工核实。", f"在根目录 README 补充{label}及可复制的操作步骤。", readme_path, 1 if readme_path else None)

    names = {PurePosixPath(p).name.lower() for p in files}
    python = bool(names & {"pyproject.toml", "requirements.txt", "setup.py", "pipfile", "environment.yml"}) or any(p.endswith(".py") for p in files)
    node = "package.json" in names or any(p.endswith((".js", ".ts", ".tsx", ".jsx")) for p in files)
    ecosystems = [label for label, present in [("Python", python), ("Node.js", node)] if present]
    manifests = names & {"pyproject.toml", "requirements.txt", "setup.py", "pipfile", "environment.yml", "package.json"}
    lock = bool(names & {"uv.lock", "poetry.lock", "pipfile.lock", "package-lock.json", "yarn.lock", "pnpm-lock.yaml"})
    lock |= any(PurePosixPath(p).name.lower().startswith("requirements") and "==" in content for p, content in files.items())
    startup = False
    for path, content in files.items():
        base = PurePosixPath(path).name.lower()
        if base == "package.json":
            try:
                package = json.loads(content)
                scripts = package.get("scripts", {}) if isinstance(package, dict) else {}
                startup |= isinstance(scripts, dict) and any(isinstance(scripts.get(k), str) and scripts[k].strip() for k in ("start", "dev", "serve"))
            except (ValueError, TypeError):
                add("MANIFEST_INVALID", "startup", "medium", "high", "依赖清单无法解析", "package.json 不是有效 JSON。", "修复清单格式。", path, 1)
        if base == "pyproject.toml":
            try:
                config = tomllib.loads(content)
                startup |= bool(config.get("project", {}).get("scripts") or config.get("tool", {}).get("poetry", {}).get("scripts"))
            except (ValueError, TypeError, AttributeError):
                add("MANIFEST_INVALID", "startup", "medium", "high", "依赖清单无法解析", "pyproject.toml 无法识别。", "修复清单格式。", path, 1)
        startup |= base in {"__main__.py", "dockerfile", "compose.yml", "compose.yaml", "docker-compose.yml", "docker-compose.yaml"}
        if path.endswith(".py"):
            startup |= bool(re.search(r'''if\s+__name__\s*==\s*["']__main__["']''', content))
    startup |= bool(re.search(r"(?i)(uvicorn\s+[\w.]+:\w+|python\s+(?:-m\s+)?[\w./-]+)", readme))
    checks = [("依赖清单", bool(manifests), 10), ("锁定版本机制", lock, 5), ("静态启动入口", startup, 10)]
    start = Dimension(key="startup", label="依赖与启动", score=sum(n for _, ok, n in checks if ok), maximum=25,
        applicable=bool(ecosystems), details=[f"{'已识别' if ok else '未识别'}：{label}（{n} 分）" for label, ok, n in checks])
    if not ecosystems:
        start.details = ["未识别到 Python / Node.js，当前不深入分析此生态，按适用维度折算总分。"]
    else:
        for label, ok, _ in checks:
            if not ok:
                add("START_" + str(len(findings)), "startup", "medium", "medium", f"未识别到{label}",
                    "此项为仓库级静态启发式检查，不证明启动成功或依赖可安装。", f"补充{label}，并在 README 说明用途。")

    path_count = windows_count = 0
    for path, content in sorted(files.items()):
        if len(findings) >= 2000:
            coverage.complete = False
            if "发现数量达到 2,000 上限，已停止后续规则检测。" not in coverage.warnings:
                coverage.warnings.append("发现数量达到 2,000 上限，已停止后续规则检测。")
            break
        ext = PurePosixPath(path).suffix.lower()
        if ext in {".bat", ".cmd", ".ps1", ".vbs"}:
            sibling = str(PurePosixPath(path).with_suffix(".sh")) in files
            documented = bool(re.search(r"(?i)(linux|macos|跨平台|unix)", readme) and re.search(r"(?i)(bash\s|sh\s|python\s|npm\s|uvicorn\s)", readme))
            alternative = sibling or documented
            windows_count += not alternative
            add("WIN_SCRIPT", "portability", "info" if alternative else "low", "high" if sibling else "medium",
                "Windows 专用脚本" + ("（识别到替代方式）" if alternative else "（需核实替代方式）"),
                "扩展名提示平台依赖，但不代表整个项目只能在 Windows 运行。", "提供同用途 .sh 或记录跨平台命令。", path, 1)
        is_env = PurePosixPath(path).name == ".env" or PurePosixPath(path).name.startswith(".env.")
        is_example = bool(re.search(r"(?i)(example|sample|template|test|fixture)", path)) or path.startswith("docs/") or ext in {".md", ".rst"}
        if is_env and not is_example:
            add("ENV_TRACKED", "secrets", "medium", "high", ".env 配置文件已入库", "文件可能包含环境特定配置，需核实敏感值。", "使用 .env.example，真实 .env 加入 .gitignore。", path, 1)
        in_private = False
        for number, line in enumerate(content.splitlines(), 1):
            if len(findings) >= 2000:
                coverage.complete = False
                coverage.warnings.append("发现数量达到 2,000 上限，已停止后续规则检测。")
                break
            # Never emit a private key's body as evidence for another rule.
            if PRIVATE.search(line):
                in_private = True
                add("PRIVATE_KEY", "secrets", "high", "high", "疑似私钥材料", "识别到私钥块起始标记。", "核实后立即轮换密钥并清理 Git 历史。", path, number)
            if in_private:
                if "END " in line and "PRIVATE KEY-----" in line:
                    in_private = False
                continue
            matches = list(TOKEN.finditer(line))
            assignment = ASSIGNMENT.search(line)
            concrete = [m for m in matches if not placeholder(m[0]) and not (
                m[0].lower().startswith("bearer ") and m[0].split(None, 1)[1].lower() in {
                    "authentication", "authorization", "credentials", "documentation", "token", "tokens"
                })]
            if concrete or literal_credential(assignment, ext):
                severity = "low" if is_example else ("high" if concrete else "medium")
                add("CREDENTIAL", "secrets", severity, "medium" if is_example or not concrete else "high",
                    "疑似硬编码凭证", "检测到具有具体值的凭证模式；未验证其有效性。", "改为环境变量；若凭证曾有效，应轮换并检查历史提交。", path, number)
            elif matches:
                add("SECRET_PLACEHOLDER", "secrets", "info", "low", "凭证占位符", "看起来是文档或模板占位符，不按泄露扣分。", "保持使用明确占位符。", path, number)
            path_line = re.sub(r'''(?:https?|ssh|git)://[^\s"'<>`]+''', "[URL]", line, flags=re.I)
            user_path = USER_PATH.search(path_line)
            absolute = ABSOLUTE.search(path_line) if ext not in {".md", ".rst", ".txt"} else None
            if user_path or absolute:
                is_user = bool(user_path)
                path_count += is_user
                add("USER_PATH" if is_user else "ABSOLUTE_PATH", "portability", "medium" if is_user else "low", "medium",
                    "疑似用户环境绝对路径" if is_user else "绝对路径需核实", "可能依赖特定用户、磁盘或运行环境。",
                    "使用相对路径、配置项或用户目录解析；标准系统路径可保留。", path, number, line)

    portability = Dimension(key="portability", label="可移植性", score=20 - min(10, path_count * 2) - min(10, windows_count * 2), maximum=20,
        details=[f"用户路径 {path_count} 处，扣 {min(10, path_count * 2)} 分", f"待核实替代方式的 Windows 脚本 {windows_count} 个，扣 {min(10, windows_count * 2)} 分", "其他绝对路径仅提示，不直接扣分。"])
    locations = {}
    for f in findings:
        if f.category == "secrets":
            key = (f.path, f.line)
            locations[key] = max(locations.get(key, 0), {"high": 15, "medium": 5, "low": 1, "info": 0}[f.severity])
    penalty = min(30, sum(locations.values()))
    secrets = Dimension(key="secrets", label="敏感信息", score=30 - penalty, maximum=30,
        details=[f"同一位置按最高严重度去重；高/中/低风险分别扣 15/5/1 分，共扣 {penalty} 分。", "仅检测当前 commit 的有限文本范围，不代表完整安全审计。"])
    dimensions = [doc, start, portability, secrets]
    applicable = [d for d in dimensions if d.applicable]
    score = round(100 * sum(d.score for d in applicable) / sum(d.maximum for d in applicable))
    urgent = any(f.category == "secrets" and f.severity == "high" and f.confidence == "high" for f in findings)
    assessment = "较易接入" if score >= 85 else "建议修复后接入" if score >= 65 else "接入阻碍较多"
    if not coverage.complete:
        assessment = "扫描不完整 · 分数暂定"
    findings.sort(key=lambda f: (LEVEL[f.severity], LEVEL.get(f.confidence, 3), f.path or "", f.line or 0))
    return Report(repository=repository, branch=branch, commit_sha=sha, scanned_at=datetime.now(timezone.utc).isoformat(),
        ecosystems=ecosystems or ["未深入支持的生态"], score=score, assessment=assessment, provisional=not coverage.complete,
        urgent=urgent, coverage=coverage, dimensions=dimensions, findings=findings)
