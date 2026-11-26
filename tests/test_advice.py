import asyncio
import json

import httpx
import pytest

from app import advice
from app.github import ScanError


REPORT = {"findings": [{"id": "f1", "category": "secrets", "severity": "high", "title": "疑似硬编码凭证", "suggestion": "核实并轮换", "path": "private/path", "evidence": "must-not-send", "source": "must-not-send"}]}


def mock_client(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(advice.httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    monkeypatch.setenv("AI_API_KEY", "synthetic-test-only")


def test_advice_only_sends_structured_findings(monkeypatch):
    def handler(request):
        assert "must-not-send" not in request.content.decode()
        assert "private/path" not in request.content.decode()
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"items": [{"finding_id": "f1", "action": "核实并轮换疑似凭证", "unexpected": "untrusted-output"}]})}}]})
    mock_client(monkeypatch, handler)
    result = asyncio.run(advice.generate_advice(REPORT))
    assert result["available"] and result["items"][0]["finding_id"] == "f1"
    assert "untrusted-output" not in str(result)


@pytest.mark.parametrize("content", ['{"items":[{"finding_id":"invented","action":"核实并轮换疑似凭证"}]}', '{"items":[{"finding_id":"f1","action":"invented free text"}]}', "not JSON", '{"items":null}'])
def test_invalid_model_output_is_rejected(monkeypatch, content):
    mock_client(monkeypatch, lambda request: httpx.Response(200, json={"choices": [{"message": {"content": content}}]}))
    with pytest.raises(ScanError, match="规则报告不受影响"):
        asyncio.run(advice.generate_advice(REPORT))


def test_model_timeout_is_safe(monkeypatch):
    def handler(request):
        raise httpx.ReadTimeout("sensitive upstream detail")
    mock_client(monkeypatch, handler)
    with pytest.raises(ScanError, match="暂时不可用"):
        asyncio.run(advice.generate_advice(REPORT))
