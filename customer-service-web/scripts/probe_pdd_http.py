"""Read-only authenticated protocol discovery. No message sends, no raw credentials in output."""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
import httpx
from desk.store import Store


async def main():
    store = Store(Path(".data"))
    shop = store.one("SELECT * FROM shops WHERE id=1")
    cookies = json.loads(store.decrypt(shop["cookies"]))
    results = []
    async with httpx.AsyncClient(
        cookies=cookies, timeout=20, follow_redirects=False, trust_env=False
    ) as client:
        for path, body in [
            ("/janus/api/new/userinfo", {}),
            ("/earth/api/merchant/queryMerchantInfoByMallId", {}),
            ("/chats/getToken", {"version": 3}),
            ("/janus/api/subSystem/getAuthToken", {"subSystemId": 17}),
            ("/plateau/sync/get_init_state", {"client_type": "WEB"}),
        ]:
            try:
                r = await client.post("https://mms.pinduoduo.com" + path, json=body)
                data = r.json()
                result = data.get("result")
                info = {
                    "path": path,
                    "http_status": r.status_code,
                    "top_keys": sorted(data),
                    "result_keys": sorted(result) if isinstance(result, dict) else None,
                    "success": data.get("success"),
                    "error_code": data.get("error_code"),
                }
                if path.endswith("getToken"):
                    info["token_present"] = bool(data.get("token") or (result or {}).get("token"))
                if path.endswith("getAuthToken"):
                    info["auth_token_present"] = bool(
                        data.get("authToken") or (result or {}).get("authToken")
                    )
                if path.endswith("get_init_state"):
                    state = result if isinstance(result, dict) else data
                    info["sequence_types"] = [v.get("seq_type") for v in state.get("init_sync_value", [])]
                results.append(info)
                print(json.dumps(info))
            except Exception as exc:
                info = {"path": path, "failure_type": type(exc).__name__}
                results.append(info)
                print(json.dumps(info))
    Path("evidence/protocol-2026-10-28/live-http-probe.json").write_text(
        json.dumps(
            {
                "time": datetime.now(timezone.utc).isoformat(),
                "requests": results,
                "note": "Identity, token and sync-state reads only; secret values omitted.",
            },
            indent=2,
        ),
        encoding="utf-8",
    )


asyncio.run(main())
