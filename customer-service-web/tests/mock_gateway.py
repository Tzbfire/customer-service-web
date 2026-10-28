"""Explicit local-only UI test model. Never used by the application by default."""

import json
from fastapi import FastAPI

app = FastAPI()


@app.post("/v1/chat/completions")
async def complete(payload: dict):
    content = payload["messages"][-1]["content"]
    if content == "请只回复 OK":
        answer = "OK"
    elif isinstance(content, list):
        answer = "界面测试样例：容量 500mL，只能手洗。"
    else:
        evidence = json.loads(content)["店铺资料"]
        answer = json.dumps(
            {
                "answer": "【界面测试响应】示例杯容量为 500mL，只能手洗，不能放入洗碗机。",
                "needs_human": False,
                "source_ids": [evidence[0]["id"]],
                "reason": "已引用明确标注为功能验证样例的资料。",
            },
            ensure_ascii=False,
        )
    return {"choices": [{"finish_reason": "stop", "message": {"content": answer}}]}
