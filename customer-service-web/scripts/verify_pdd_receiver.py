"""Exercise the production receiver against live PDD, with all received messages captured as counts only.

No model calls, no buyer messages, no business-state changes. Uses an isolated temporary database.
"""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from desk.channels import Channels
from desk.store import Store


async def main():
    source = Store(Path(".data"))
    shop = source.one("SELECT * FROM shops WHERE id=1")
    cookies = source.decrypt(shop["cookies"])
    with TemporaryDirectory(prefix="receiver-check-", dir=source.directory) as folder:
        isolated = Store(Path(folder))
        isolated.execute(
            "INSERT INTO shops(id,name,platform_shop_id,platform_user_id,cookies,mode,created_at) VALUES(1,?,?,?,?,?,0)",
            (
                "protocol verification",
                shop["platform_shop_id"],
                shop["platform_user_id"],
                isolated.encrypt(cookies),
                "manual",
            ),
        )
        channels = Channels(isolated, None)
        seen = {"realtime": 0, "recovered": 0}

        async def count_only(shop_id, item):
            seen["recovered" if item.get("recovered") else "realtime"] += 1

        channels.receive = count_only
        task = asyncio.create_task(channels._run_connection(1))
        try:
            for _ in range(80):
                await asyncio.sleep(0.25)
                if task.done():
                    task.result()
                state = channels.states.get(1, {})
                if state.get("last_heartbeat") and state.get("sync") == "ok":
                    # Stay through another sync/heartbeat cycle to verify ongoing task coordination.
                    await asyncio.sleep(28)
                    if task.done():
                        task.result()
                    state = channels.states[1]
                    report = {
                        "time": datetime.now(timezone.utc).isoformat(),
                        "state": state["status"],
                        "build": state["build"],
                        "transport": state["transport"],
                        "heartbeat_observed": bool(state.get("last_heartbeat")),
                        "sequence_sync": state["sync"],
                        "observed_message_counts": seen,
                        "buyer_messages_sent": 0,
                        "note": "Production receiver with count-only delivery; no model or sender callbacks.",
                    }
                    print(json.dumps(report))
                    Path("evidence/protocol-2026-10-28/receiver-integration-check.json").write_text(
                        json.dumps(report, indent=2), encoding="utf-8"
                    )
                    return
            raise RuntimeError("receiver did not reach an authenticated heartbeat and sync state")
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


asyncio.run(main())
