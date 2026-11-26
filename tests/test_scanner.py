import io
import json
import zipfile

import pytest

from app import github
from app.models import Coverage
from app.scanner import scan


def report(files, complete=True):
    return scan(files, "example/project", "main", "a" * 40, Coverage(scanned_files=len(files), complete=complete))


def archive(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as z:
        for name, content in entries.items():
            z.writestr("repo-main/" + name, content)
    return stream.getvalue()


COMPLETE = {
    "README.md": "# 项目介绍\n这是一个用于测试接入完整度的示例项目。\n## 环境要求\nPython 3.12\n## 安装\npip install -r requirements.txt\n## 启动\npython main.py\n## 配置\n使用环境变量\n",
    "requirements.txt": "fastapi==0.165.0\n",
    "main.py": 'if __name__ == "__main__":\n    print("hello")\n',
}


def test_complete_project_exact_score():
    r = report(COMPLETE)
    assert r.score == 100
    assert not r.findings
    assert r.assessment == "较易接入"


def test_risky_project_exact_score_and_no_secrets():
    synthetic = "sk-" + "Q7a9B2c4D6e8F0g1H3j5"
    files = COMPLETE | {"start.bat": 'cd C:\\Users\\Alice\\app\n', ".env": "API_KEY=" + synthetic}
    r = report(files)
    # 4 portability points + 15 secrets, same line .env and key deduct once.
    assert r.score == 81
    assert r.urgent
    assert synthetic not in r.model_dump_json()
    f = next(f for f in r.findings if f.rule_id == "CREDENTIAL")
    assert f.path == ".env" and f.line == 1
    assert f.source_url.endswith("/.env#L1")


def test_placeholders_and_routes_are_not_high_risk():
    files = COMPLETE | {".env.example": "API_KEY=sk-your_example_key\nTOKEN=Bearer <TOKEN>\n", "routes.py": '@app.get("/api/v1/users")\n# Bearer authentication\n# sk-\n#!/usr/bin/env python\n'}
    r = report(files)
    assert not any(f.severity == "high" for f in r.findings)
    assert not any(f.rule_id in {"USER_PATH", "ABSOLUTE_PATH"} for f in r.findings)
    assert r.score == 100


def test_private_key_body_never_becomes_evidence():
    body = "secret-key-material C:\\Users\\Alice\\key"
    r = report(COMPLETE | {"key.pem": "-----BEGIN PRIVATE KEY-----\n" + body + "\n-----END PRIVATE KEY-----"})
    assert r.urgent
    assert body not in r.model_dump_json()
    assert not any(f.rule_id == "USER_PATH" for f in r.findings)


def test_other_secret_on_matched_line_is_not_leaked():
    r = report(COMPLETE | {"config.py": 'password="arbitrary-not-recognized"; path="C:\\Users\\a"'})
    assert "arbitrary-not-recognized" not in r.model_dump_json()


def test_shell_alternative_prevents_windows_deduction():
    r = report(COMPLETE | {"scripts/run.ps1": "echo ok", "scripts/run.sh": "echo ok"})
    assert r.score == 100
    assert next(f for f in r.findings if f.rule_id == "WIN_SCRIPT").severity == "info"


def test_unsupported_ecosystem_and_partial_result():
    r = report({"main.go": "package main"}, complete=False)
    assert not r.dimensions[1].applicable
    assert r.provisional and r.assessment == "扫描不完整 · 分数暂定"
    assert r.score == 67  # 50 / 75, rounded.


def test_invalid_json_is_a_finding_not_crash():
    r = report({"package.json": "not JSON"})
    assert any(f.rule_id == "MANIFEST_INVALID" for f in r.findings)


def test_secret_key_and_environment_reference():
    r = report(COMPLETE | {"settings.py": 'SECRET_KEY="synthetic-hardcoded"\npassword=os.getenv("PASSWORD")\n'})
    secrets = [f for f in r.findings if f.rule_id == "CREDENTIAL"]
    assert len(secrets) == 1 and secrets[0].line == 1
    assert "synthetic-hardcoded" not in r.model_dump_json()


def test_real_repository_false_positive_regression():
    r = report(COMPLETE | {"links.py": 'url="https://example.com/home/user"\n# flask-sqlalchemy extension\n# flask-authentication\n'})
    assert not r.findings
    assert r.score == 100


def test_annotations_and_dynamic_assignments_are_not_credentials():
    r = report(COMPLETE | {"settings.py": 'secret_key: str | None\npassword=request.form["password"]\npassword=make_password()\n'})
    assert not r.findings


def test_findings_limit_marks_result_provisional():
    r = report(COMPLETE | {"config.py": 'path="C:\\Users\\person"\n' * 2100})
    assert r.provisional and r.coverage.warnings
    assert len(r.findings) == 2000


@pytest.mark.parametrize("url", ["http://github.com/a/b", "https://github.com.evil/a/b", "https://github.com@evil.com/a/b", "https://github.com/a/b/tree/main", "https://github.com/a/b?x=1", "https://github.com:443/a/b", "https://github.com/a/.."])
def test_url_validation_rejects_arbitrary_fetch(url):
    with pytest.raises(ValueError):
        github.parse_repository(url)


def test_url_normalization():
    assert github.parse_repository(" https://github.com/owner/repo.git/ ") == "owner/repo"


def test_archive_encoding_skip_and_traversal():
    files, c = github.read_archive(archive({"启动.ps1": "Write-Output '你好'".encode("utf-16"), "README.md": "你好", "node_modules/x.js": "skip", "image.png": b"image", "../escape": "bad"}))
    assert "启动.ps1" in files and "你好" in files["启动.ps1"]
    assert c.scanned_files == 2
    assert not c.complete
    assert c.skipped["不安全路径"] == 1
    assert c.skipped["依赖或构建目录"] == 1


def test_oversized_text_marks_partial(monkeypatch):
    monkeypatch.setattr(github, "MAX_FILE_BYTES", 10)
    files, c = github.read_archive(archive({"huge.txt": "a" * 11}))
    assert not files and not c.complete


def test_archive_limits_are_explicit(monkeypatch):
    data = archive({"a.txt": "one", "b.txt": "two"})
    monkeypatch.setattr(github, "MAX_ENTRIES", 1)
    files, c = github.read_archive(data)
    assert len(files) == 1 and not c.complete and c.warnings
    monkeypatch.setattr(github, "MAX_ENTRIES", 20_000)
    monkeypatch.setattr(github, "MAX_EXPANDED", 4)
    files, c = github.read_archive(data)
    assert len(files) == 1 and not c.complete and c.warnings


def test_bad_archive_is_safe_error():
    with pytest.raises(github.ScanError):
        github.read_archive(b"not zip")


def test_routes_and_windows_case():
    from app.scanner import USER_PATH
    assert USER_PATH.search(r"\\SERVER\Share\project")
    r = report(COMPLETE | {"routes.py": '@app.get("/users/me")\n@app.get("/users/page/<int:page>")\n'})
    assert not any(f.rule_id == "USER_PATH" for f in r.findings)
    r = report(COMPLETE | {"run.cmd": "cd C:\\Users\\Alice"})
    assert any(f.rule_id == "USER_PATH" for f in r.findings)


def test_documentation_paths_do_not_deduct():
    r = report(COMPLETE | {"docs/setup.md": "/Users/alice/project\n/home/alice/project"})
    assert r.dimensions[2].score == 20
    assert all(f.severity == "info" for f in r.findings)


def test_low_risk_secret_deduction_capped():
    r = report(COMPLETE | {"tests/settings.txt": "\n".join('password="synthetic-value"' for _ in range(20))})
    assert r.dimensions[3].score == 25


def test_unassessed_ecosystem_cannot_claim_ready():
    r = report({"README.md": COMPLETE["README.md"], "main.go": "package main"})
    assert r.provisional
    assert "未评估" in r.assessment
    assert r.assessment != "较易接入"


def test_document_rule_ids_stable():
    r = report({"main.py": "pass"})
    assert "DOC_INSTALL_MISSING" in {f.rule_id for f in r.findings}
