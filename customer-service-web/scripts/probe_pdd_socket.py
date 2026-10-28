"""Read-only legacy socket verification. No messages sent; only explicit heartbeat command."""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
import httpx
import websockets
from desk.store import Store


async def probe(client, version):
    response = await client.post("https://mms.pinduoduo.com/chats/getToken", json={"version": 3})
    data = response.json()
    token = data.get("token") or (data.get("result") or {}).get("token")
    record = {
        "version": version,
        "http_status": response.status_code,
        "token_present": bool(token),
        "frames": [],
    }
    if not token:
        return record
    query = urlencode({"access_token": token, "role": "mall_cs", "client": "web", "version": version})
    try:
        async with websockets.connect(
            "wss://m-ws.pinduoduo.com/?" + query,
            open_timeout=15,
            compression=None,
            ping_interval=None,
            proxy=None,
        ) as ws:
            record["handshake"] = "accepted"
            for index in range(4):
                try:
                    raw = await asyncio.wait_for(ws.recv(), 8 if index == 0 else 3)
                except asyncio.TimeoutError:
                    break
                if isinstance(raw, bytes):
                    record["frames"].append({"kind": "binary", "bytes": len(raw)})
                    continue
                frame = json.loads(raw)
                detail = {"kind": "json", "keys": sorted(frame), "response": frame.get("response")}
                if frame.get("response") == "auth":
                    auth = frame.get("auth") or {}
                    detail["auth_keys"] = sorted(auth)
                    detail["auth_result"] = auth.get("result") or frame.get("result")
                    detail["status"] = auth.get("status") or frame.get("status")
                record["frames"].append(detail)
            await ws.send(json.dumps({"cmd": "heartbeat", "request_id": 999000001}))
            try:
                raw = await asyncio.wait_for(ws.recv(), 5)
                if isinstance(raw, str):
                    frame = json.loads(raw)
                    record["heartbeat_response"] = {
                        "keys": sorted(frame),
                        "response": frame.get("response"),
                        "result": frame.get("result"),
                    }
            except asyncio.TimeoutError:
                record["heartbeat_response"] = "timeout"
    except Exception as exc:
        record["failure_type"] = type(exc).__name__
    return record


async def main():
    store = Store(Path(".data"))
    shop = store.one("SELECT cookies FROM shops WHERE id=1")
    cookies = json.loads(store.decrypt(shop["cookies"]))
    async with httpx.AsyncClient(cookies=cookies, timeout=20, trust_env=False) as client:
        results = []
        for version in ["202506091557", "202610261554"]:
            record = await probe(client, version)
            results.append(record)
            print(json.dumps(record))
    Path("evidence/protocol-2026-10-28/live-websocket-probe.json").write_text(
        json.dumps(
            {
                "time": datetime.now(timezone.utc).isoformat(),
                "results": results,
                "note": "No buyer messages sent. Frame structure and auth verdicts only; credentials omitted.",
            },
            indent=2,
        ),
        encoding="utf-8",
    )


asyncio.run(main())
