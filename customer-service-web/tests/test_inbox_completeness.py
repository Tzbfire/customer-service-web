import json

import httpx
import pytest
from fastapi.testclient import TestClient

from desk.app import create_app

H = {"X-Desk-Request": "1"}


@pytest.fixture
def inbox(tmp_path):
    calls = []
    behavior = {"fail": False}

    def message(index, role="user", kind=0):
        return {
            "msg_id": f"m{index}",
            "type": kind,
            "ts": 1000 + index,
            "content": "https://img.pddpic.com/a.jpg" if kind == 1 else f"Message {index}",
            "from": {"role": role, "uid": "buyer" if role == "user" else "staff"},
            "to": {
                "role": "mall_cs" if role == "user" else "user",
                "uid": "staff" if role == "user" else "buyer",
            },
        }

    def platform(request):
        payload = json.loads(request.content)
        calls.append((request.url.path, payload))
        if request.url.path.endswith("userInfoWithTags"):
            return httpx.Response(
                200, json={"success": True, "result": {"uid": "buyer", "nickname": "Buyer"}}
            )
        assert request.url.path == "/plateau/chat/list", (
            "Completeness must not call sending or model endpoints"
        )
        if behavior["fail"]:
            return httpx.Response(503)
        options = payload["data"]["list"]
        assert payload["data"]["notUpdateUnreplyTs"] is True
        older = options.get("start_msg_id")
        custom = behavior.get("pages", {}).get(older or "latest")
        if custom:
            indices, has_more = custom
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "result": {
                        "messages": [
                            message(i, "mall_cs" if i % 2 == 0 else "user") for i in reversed(indices)
                        ],
                        "has_more": has_more,
                    },
                },
            )
        messages = (
            [message(1), message(2, "mall_cs", 1)]
            if older
            else [message(3), message(4, "mall_cs"), message(5), message(6, "mall_cs")]
        )
        return httpx.Response(
            200,
            json={
                "success": True,
                "result": {"messages": list(reversed(messages)), "has_more": not bool(older)},
            },
        )

    app = create_app(tmp_path, channel_transport=httpx.MockTransport(platform))
    with TestClient(app) as client:
        client.post("/api/setup", headers=H, json={"username": "owner", "password": "fixture-password-123"})
        store = app.state.store
        store.execute(
            "UPDATE shops SET cookies=? WHERE id=1", (store.encrypt(json.dumps({"fixture": "cookie"})),)
        )
        cid = store.execute(
            "INSERT INTO conversations(shop_id,buyer_uid,buyer_name,mode,assignee,needs_human,updated_at) VALUES(1,'buyer','Buyer','human',1,0,2000)"
        )
        store.execute(
            "INSERT INTO messages(conversation_id,role,content,state,platform_id,created_at) VALUES(?,'customer','Message 5','received','m5',1005)",
            (cid,),
        )
        yield client, app, cid, calls, behavior


def test_live_transcript_backfills_both_directions_without_taking_over_or_replying(inbox):
    client, app, cid, calls, _ = inbox
    page = client.get(f"/api/conversations/{cid}").json()
    assert [m["platform_id"] for m in page["messages"]] == ["m3", "m4", "m5", "m6"]
    assert page["platform_history"]["has_more"] is True
    assert page["platform_history"]["status"] == "ok"
    conv = app.state.store.one(
        "SELECT mode,assignee,needs_human,updated_at FROM conversations WHERE id=?", (cid,)
    )
    assert conv == {"mode": "human", "assignee": 1, "needs_human": 0, "updated_at": 2000}
    assert not app.state.store.one("SELECT id FROM messages WHERE role IN ('draft','assistant')")
    before = len(calls)
    client.get(f"/api/conversations/{cid}")
    assert len(calls) == before, "Polling should reuse the short-lived sync checkpoint"


def test_earlier_platform_messages_continue_into_live_transcript_without_duplicates(inbox):
    client, app, cid, calls, _ = inbox
    client.get(f"/api/conversations/{cid}")
    result = client.post(f"/api/conversations/{cid}/history", headers=H, json={"older": True})
    assert result.status_code == 200, result.text
    page = client.get(f"/api/conversations/{cid}").json()
    assert [m["platform_id"] for m in page["messages"]] == [f"m{i}" for i in range(1, 7)]
    assert page["platform_history"]["has_more"] is False and page["total"] == 6
    assert page["messages"][1]["presentation"]["kind"] == "image"
    client.post(f"/api/conversations/{cid}/history", headers=H, json={"older": True})
    assert app.state.store.one("SELECT count(*) n FROM messages WHERE conversation_id=?", (cid,))["n"] == 6


def test_platform_sync_failure_keeps_local_transcript_and_exposes_retry_status(inbox):
    client, app, cid, _, behavior = inbox
    behavior["fail"] = True
    page = client.get(f"/api/conversations/{cid}").json()
    assert page["messages"][0]["platform_id"] == "m5"
    assert page["platform_history"]["status"] == "error"
    assert page["platform_history"]["complete"] is False
    behavior["fail"] = False
    assert (
        client.post(f"/api/conversations/{cid}/history", headers=H, json={"older": False}).status_code == 200
    )
    page = client.get(f"/api/conversations/{cid}").json()
    assert len(page["messages"]) == 4 and page["platform_history"]["status"] == "ok"


def test_disconnection_gap_is_filled_without_losing_older_pagination(inbox):
    client, app, cid, calls, behavior = inbox
    client.get(f"/api/conversations/{cid}")
    behavior["pages"] = {"latest": ([9, 10], True), "m9": ([6, 7, 8], True)}
    result = client.post(f"/api/conversations/{cid}/history", headers=H, json={"older": False}).json()
    assert result["pending_gap"] is True
    result = client.post(f"/api/conversations/{cid}/history", headers=H, json={"older": False}).json()
    assert result["pending_gap"] is False and result["has_more"] is True
    client.post(f"/api/conversations/{cid}/history", headers=H, json={"older": True})
    page = client.get(f"/api/conversations/{cid}").json()
    assert [m["platform_id"] for m in page["messages"]] == [f"m{i}" for i in range(1, 11)]
    assert page["platform_history"]["complete"] is True
    assert app.state.store.one("SELECT needs_human FROM conversations WHERE id=?", (cid,))["needs_human"] == 0


def test_existing_withdrawal_is_not_replaced_by_historical_original(inbox):
    client, app, cid, _, _ = inbox
    app.state.store.execute(
        "UPDATE messages SET presentation=? WHERE conversation_id=?",
        (json.dumps({"version": 1, "kind": "withdrawn", "body": "消息已撤回"}), cid),
    )
    page = client.get(f"/api/conversations/{cid}").json()
    original = next(m for m in page["messages"] if m["platform_id"] == "m5")
    assert original["presentation"]["kind"] == "withdrawn"


def test_local_pagination_does_not_refetch_or_hide_platform_cursor(inbox):
    client, app, cid, calls, _ = inbox
    first = client.get(f"/api/conversations/{cid}?limit=2").json()
    assert [m["platform_id"] for m in first["messages"]] == ["m5", "m6"]
    assert first["has_more"] is True
    count = len(calls)
    older = client.get(f"/api/conversations/{cid}?before_id={first['next_before_id']}&limit=2").json()
    assert [m["platform_id"] for m in older["messages"]] == ["m3", "m4"]
    assert len(calls) == count and older["platform_history"]["has_more"] is True


def test_history_sync_requires_login_and_valid_conversation(inbox):
    client, app, cid, _, _ = inbox
    assert client.post("/api/conversations/999/history", headers=H, json={"older": False}).status_code == 404
    assert client.post(f"/api/conversations/{cid}/history", json={"older": False}).status_code == 403
    client.post("/api/logout", headers=H)
    assert (
        client.post(f"/api/conversations/{cid}/history", headers=H, json={"older": False}).status_code == 401
    )


def test_backfilling_old_records_during_ai_generation_does_not_make_them_latest(inbox):
    client, app, cid, _, _ = inbox
    app.state.store.execute("UPDATE conversations SET mode='ai',assignee=NULL WHERE id=?", (cid,))

    async def answer(*args, **kwargs):
        app.state.store.execute(
            "INSERT INTO messages(conversation_id,role,content,state,platform_id,created_at) VALUES(?,'customer','Older message','historical','m1',1001)",
            (cid,),
        )
        return {"answer": "Fixture draft", "sources": [{"id": 1}], "needs_human": False}

    app.state.knowledge.answer = answer
    item = {
        "uid": "buyer",
        "name": "Buyer",
        "goods_id": "",
        "content": "New question",
        "platform_id": "new-question",
        "timestamp": 1006,
        "automatic": True,
    }
    client.portal.call(app.state.channels.receive, 1, item)
    assert app.state.store.one("SELECT count(*) n FROM messages WHERE role='draft'")["n"] == 1


def test_history_refresh_cannot_consume_a_fresh_live_message_before_the_receiver(inbox):
    from time import time

    client, app, cid, _, _ = inbox
    now = time()
    app.state.store.execute("UPDATE conversations SET mode='ai',assignee=NULL WHERE id=?", (cid,))
    app.state.store.execute(
        "INSERT INTO messages(conversation_id,role,content,state,platform_id,created_at) VALUES(?,'customer','Just received','historical','fresh',?)",
        (cid, now),
    )
    called = []

    async def answer(*args, **kwargs):
        called.append(True)
        return {"answer": "Fixture draft", "sources": [{"id": 1}], "needs_human": False}

    app.state.knowledge.answer = answer
    item = {
        "uid": "buyer",
        "name": "Buyer",
        "goods_id": "",
        "content": "Just received",
        "platform_id": "fresh",
        "timestamp": now,
        "automatic": True,
    }
    client.portal.call(app.state.channels.receive, 1, item)
    client.portal.call(app.state.channels.receive, 1, item)
    assert len(called) == 1
    assert app.state.store.one("SELECT state FROM messages WHERE platform_id='fresh'")["state"] == "received"
