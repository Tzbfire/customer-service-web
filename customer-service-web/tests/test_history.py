import json
import httpx
import pytest
from fastapi.testclient import TestClient
from desk.app import create_app

H = {"X-Desk-Request": "1"}


@pytest.fixture
def history_system(tmp_path):
    requests = []

    def platform(request):
        payload = json.loads(request.content)
        requests.append((request.url.path, payload))
        if request.url.path == "/latitude/user/userInfoWithTags":
            return httpx.Response(
                200, json={"success": True, "result": {"uid": payload["uid"], "nickname": "Test buyer"}}
            )
        command = payload["data"]
        if request.url.path.endswith("latest_conversations"):
            page = command["page"]
            messages = (
                [
                    {
                        "type": 0,
                        "msg_id": "90071992547409930",
                        "ts": 1003,
                        "content": "recent question",
                        "from": {"role": "user", "uid": "buyer1"},
                        "to": {"role": "mall_cs", "uid": "staff"},
                        "info": {"goodsID": "product1", "goodsName": "Test item"},
                    }
                ]
                if page == 1
                else []
            )
            return httpx.Response(
                200, json={"success": True, "result": {"conversations": messages, "has_more": page == 1}}
            )
        if request.url.path.endswith("/chat/list"):
            options = command["list"]
            assert command["notUpdateUnreplyTs"] is True
            if options["with"] == {"role": "user", "id": "buyer2"}:
                return httpx.Response(
                    200, json={"success": True, "result": {"messages": [], "has_more": False}}
                )
            assert options["with"] == {"role": "user", "id": "buyer1"}
            messages = (
                [
                    {
                        "type": 0,
                        "msg_id": "m3",
                        "ts": 1003,
                        "content": "question",
                        "from": {"role": "user", "uid": "buyer1"},
                    },
                    {
                        "type": 0,
                        "msg_id": "m2",
                        "ts": 1002,
                        "content": "reply",
                        "from": {"role": "mall_cs", "uid": "staff"},
                    },
                ]
                if "start_msg_id" not in options
                else [
                    {
                        "type": 1,
                        "msg_id": "m1",
                        "ts": 1001,
                        "content": "https://img.pddpic.com/test.png",
                        "from": {"role": "user", "uid": "buyer1"},
                    }
                ]
            )
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "result": {"messages": messages, "has_more": "start_msg_id" not in options},
                },
            )
        raise AssertionError("History browsing must not call send or AI endpoints.")

    app = create_app(tmp_path, channel_transport=httpx.MockTransport(platform))
    with TestClient(app) as client:
        client.post("/api/setup", headers=H, json={"username": "owner", "password": "history-test-password"})
        app.state.store.execute(
            "UPDATE shops SET cookies=? WHERE id=1",
            (app.state.store.encrypt(json.dumps({"fixture": "cookie"})),),
        )
        yield client, app, requests


def test_conversation_page_preserves_large_ids_and_does_not_import_or_reply(history_system):
    client, app, requests = history_system
    response = client.get("/api/history/conversations", params={"shop_id": 1, "page": 1, "size": 20})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["has_more"] is True and result["total"] is None
    assert result["items"][0]["buyer_uid"] == "buyer1"
    assert result["items"][0]["last_message_id"] == "90071992547409930"
    assert result["items"][0]["goods_id"] == "product1"
    assert requests[0][1]["data"]["page"] == 1
    assert app.state.store.one("SELECT COUNT(*) n FROM messages")["n"] == 0


def test_history_messages_have_original_times_directions_and_exclusive_cursor(history_system):
    client, app, requests = history_system
    result = client.get(
        "/api/history/messages", params={"shop_id": 1, "buyer_uid": "buyer1", "size": 2}
    ).json()
    assert [item["platform_id"] for item in result["messages"]] == ["m2", "m3"]
    assert [item["role"] for item in result["messages"]] == ["agent", "customer"]
    assert result["next_cursor"] == "m2" and result["has_more"] is True
    older = client.get(
        "/api/history/messages", params={"shop_id": 1, "buyer_uid": "buyer1", "size": 2, "before": "m2"}
    ).json()
    assert older["messages"][0]["platform_id"] == "m1"
    assert older["messages"][0]["attachments"][0]["url"] == "https://img.pddpic.com/test.png"
    assert older["has_more"] is False
    assert requests[-1][1]["data"]["list"]["start_msg_id"] == "m2"
    assert app.state.store.one("SELECT COUNT(*) n FROM messages")["n"] == 0


def test_importing_history_is_idempotent_and_only_takes_over_on_explicit_action(history_system):
    client, app, _ = history_system
    payload = {"shop_id": 1, "buyer_uid": "buyer1", "buyer_name": "Test buyer"}
    first = client.post("/api/history/open", headers=H, json=payload)
    assert first.status_code == 200, first.text
    identifier = first.json()["conversation_id"]
    assert client.post("/api/history/open", headers=H, json=payload).json()["conversation_id"] == identifier
    conversation = app.state.store.one("SELECT * FROM conversations WHERE id=?", (identifier,))
    assert conversation["mode"] == "human" and conversation["assignee"] == 1
    rows = app.state.store.all(
        "SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at", (identifier,)
    )
    assert len(rows) == 2 and all(row["state"] == "historical" for row in rows)
    assert [row["content"] for row in rows] == ["reply", "question"]
    assert not app.state.store.one("SELECT id FROM messages WHERE role IN ('assistant','draft')")


def test_history_requires_auth_valid_shop_and_bounded_pagination(history_system):
    client, app, _ = history_system
    assert client.get("/api/history/conversations", params={"shop_id": 99}).status_code == 404
    assert client.get("/api/history/conversations", params={"shop_id": 1, "page": 0}).status_code == 422
    assert (
        client.get(
            "/api/history/messages", params={"shop_id": 1, "buyer_uid": "buyer1", "size": 1000}
        ).status_code
        == 422
    )
    client.post("/api/logout", headers=H)
    assert client.get("/api/history/conversations", params={"shop_id": 1}).status_code == 401


def test_local_transcript_pagination_is_chronological_not_import_order(history_system):
    client, app, _ = history_system
    cid = app.state.store.execute(
        "INSERT INTO conversations(shop_id,buyer_uid,buyer_name,updated_at) VALUES(1,?,?,0)",
        ("buyer2", "Second buyer"),
    )
    for timestamp in (1003, 1001, 1002):
        app.state.store.execute(
            "INSERT INTO messages(conversation_id,role,content,created_at) VALUES(?,?,?,?)",
            (cid, "customer", str(timestamp), timestamp),
        )
    page = client.get(f"/api/conversations/{cid}", params={"limit": 2}).json()
    assert [m["content"] for m in page["messages"]] == ["1002", "1003"]
    assert page["has_more"] is True and page["total"] == 3
    older = client.get(
        f"/api/conversations/{cid}", params={"limit": 2, "before_id": page["next_before_id"]}
    ).json()
    assert [m["content"] for m in older["messages"]] == ["1001"]
    assert older["has_more"] is False
