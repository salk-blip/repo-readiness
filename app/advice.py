import json
import os

import httpx

from .github import ScanError


async def generate_advice(report: dict) -> dict:
    if not os.getenv("AI_API_KEY"):
        return {"available": False, "message": "未配置 AI_API_KEY。规则报告已完整生成。", "items": []}
    findings = report["findings"][:30]
    # No repository source, evidence, paths or credentials are sent to the model.
    payload = [{k: f[k] for k in ("id", "category", "severity", "title", "suggestion")} for f in findings]
    if not payload:
        return {"available": True, "message": "没有待处理发现，建议按 README 在隔离环境中验证启动。", "items": []}
    system = (
        "你是项目接入助手。只依据提供的结构化静态发现，最多返回三项中文建议。"
        "不得声称已执行项目，不得更改评分。输出 JSON 对象，items 数组中每项只含 finding_id 与 action。"
        "action 只能选择：补充接入文档、核实启动与依赖、移除个人环境依赖、核实并轮换疑似凭证。"
    )
    try:
        async with httpx.AsyncClient(timeout=25, follow_redirects=False) as client:
            r = await client.post(os.getenv("AI_BASE_URL", "https://api.openai.com/v1").rstrip("/") + "/chat/completions",
                headers={"Authorization": "Bearer " + os.environ["AI_API_KEY"]},
                json={"model": os.getenv("AI_MODEL", "gpt-4.1-mini"), "messages": [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}], "response_format": {"type": "json_object"}, "max_tokens": 600})
            r.raise_for_status()
            result = json.loads(r.json()["choices"][0]["message"]["content"])
        allowed_ids = {f["id"] for f in findings}
        allowed_actions = {"补充接入文档", "核实启动与依赖", "移除个人环境依赖", "核实并轮换疑似凭证"}
        items = result["items"]
        if not isinstance(items, list) or len(items) > 3 or any(not isinstance(i, dict) or i.get("finding_id") not in allowed_ids or i.get("action") not in allowed_actions for i in items):
            raise ValueError("invalid advice")
        # Reconstruct instead of persisting untrusted model-generated prose.
        return {"available": True, "message": "AI 优先级建议，仅供参考；未改变规则评分。", "items": [{"finding_id": i["finding_id"], "action": i["action"]} for i in items]}
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
        raise ScanError("AI 建议暂时不可用，规则报告不受影响。") from None
