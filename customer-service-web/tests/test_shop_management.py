import asyncio
import csv
import io
import json
import time
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient

from desk.app import create_app
from desk.store import password_hash

H = {"X-Desk-Request": "1"}


@pytest.fixture
def managed(tmp_path):
    calls = []
    behavior = {"fail": False}

    def platform(request):
        path = request.url.path
        payload = json.loads(request.content)
        calls.append((path, payload))
        if path.endswith("userInfoWithTags"):
            return httpx.Response(
                200, json={"success": True, "result": {"uid": payload["uid"], "nickname": "Buyer"}}
            )
        if path.endswith("latest_conversations"):
            page = payload["data"]["page"]
            uid = "buyer1" if page == 1 else "buyer2"
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "result": {
                        "conversations": [
                            {
                                "type": 0,
                                "from": {"role": "user", "uid": uid},
                                "to": {"role": "mall_cs", "uid": "staff"},
                                "content": "Latest",
                                "msg_id": "90071992547409930",
                                "user_info": {"uid": uid, "nickname": "Buyer " + uid},
                            }
                        ],
                        "has_more": page == 1,
                    },
                },
            )
        if path.endswith("/chat/list"):
            options = payload["data"]["list"]
            uid = options["with"]["id"]
            older = "start_msg_id" in options
            if behavior["fail"] and older:
                return httpx.Response(503)
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "result": {
                        "messages": [
                            {
                                "type": 1 if older else 0,
                                "from": {"role": "user", "uid": uid},
                                "to": {"role": "mall_cs", "uid": "staff"},
                                "content": "https://img.pddpic.com/photo.jpg"
                                if older
                                else "<script>alert(1)</script>",
                                "msg_id": "90071992547409929" if older else "90071992547409930",
                                "ts": 100 if older else 200,
                            }
                        ],
                        "has_more": not older,
                    },
                },
            )
        raise AssertionError("Export must not send messages or call a model: " + path)

    app = create_app(tmp_path, channel_transport=httpx.MockTransport(platform))
    with TestClient(app) as client:
        client.post("/api/setup", headers=H, json={"username": "owner", "password": "test-only-password"})
        app.state.store.execute(
            "UPDATE shops SET name='Test shop',cookies=?,platform_shop_id='mall1' WHERE id=1",
            (app.state.store.encrypt(json.dumps({"fixture": "DO-NOT-EXPORT-COOKIE"})),),
        )
        yield client, app, calls, behavior


def wait_job(client, job):
    for _ in range(200):
        current = client.get(f"/api/shops/1/exports/{job['id']}").json()
        if current["status"] != "running":
            return current
        time.sleep(0.015)
    raise AssertionError("Export did not finish")


def add_local(app, uid="buyer1", platform_id="90071992547409930", content="Local content"):
    cid = app.state.store.execute(
        "INSERT INTO conversations(shop_id,buyer_uid,buyer_name,updated_at) VALUES(1,?,?,0)", (uid, "Buyer")
    )
    app.state.store.execute(
        "INSERT INTO messages(conversation_id,role,content,platform_id,created_at) VALUES(?,'customer',?,?,200)",
        (cid, content, platform_id),
    )
    return cid


def test_platform_export_reads_all_pages_merges_local_and_omits_credentials(managed):
    client, app, calls, _ = managed
    add_local(app)
    cid = app.state.store.one("SELECT id FROM conversations")["id"]
    app.state.store.execute(
        "INSERT INTO messages(conversation_id,role,content,state,created_at) VALUES(?,'draft','=SUM(1,2)','draft',150)",
        (cid,),
    )
    result = client.post("/api/shops/1/exports", headers=H, json={"scope": "platform"})
    assert result.status_code == 200, result.text
    job = wait_job(client, result.json())
    assert job["status"] == "done", job
    assert job["conversations"] == 2 and job["completed"] == 2 and job["messages"] == 5
    response = client.get(f"/api/shops/1/exports/{job['id']}/download")
    assert response.status_code == 200 and response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.testzip() is None
        assert {
            "manifest.json",
            "index.html",
            "messages.csv",
            "README.txt",
            "index.md",
            "messages.md",
        } <= set(archive.namelist())
        markdown_files = [
            n for n in archive.namelist() if n.startswith("conversations/") and n.endswith(".md")
        ]
        assert len(markdown_files) == job["conversations"]
        assert archive.read("messages.md").decode("utf-8").count("消息 ID：") == 4
        assert "![聊天图片]" in archive.read("conversations/000001.md").decode("utf-8")
        combined = b"".join(archive.read(name) for name in archive.namelist())
        assert b"DO-NOT-EXPORT-COOKIE" not in combined and b"api_key" not in combined
        data = json.loads(archive.read("conversations/000001.json"))
        assert len(data["messages"]) == 3
        assert data["messages"][-1]["platform_id"] == "90071992547409930"
        assert b"<script>alert(1)</script>" not in archive.read("conversations/000001.html")
        assert b"&lt;script&gt;" in archive.read("conversations/000001.html")
        rows = list(csv.reader(io.StringIO(archive.read("messages.csv").decode("utf-8-sig"))))
        assert any(row[6] == "'=SUM(1,2)" for row in rows[1:])
    markdown_response = client.get(f"/api/shops/1/exports/{job['id']}/download?format=markdown")
    assert (
        markdown_response.status_code == 200 and "text/markdown" in markdown_response.headers["content-type"]
    )
    assert markdown_response.text.count("消息 ID：") == 4
    assert len([p for p, b in calls if p.endswith("latest_conversations")]) == 2
    assert app.state.store.one("SELECT count(*) n FROM messages")["n"] == 2
    assert app.state.store.one("SELECT mode FROM conversations")["mode"] == "ai"


def test_local_export_works_without_login_or_platform_requests(managed):
    client, app, calls, _ = managed
    add_local(app, platform_id=None)
    app.state.store.execute("UPDATE shops SET cookies='' WHERE id=1")
    job = wait_job(client, client.post("/api/shops/1/exports", headers=H, json={"scope": "local"}).json())
    assert job["status"] == "done" and job["messages"] == 1 and not calls


def test_failed_export_resumes_from_saved_message_cursor(managed):
    client, app, calls, behavior = managed
    behavior["fail"] = True
    job = wait_job(client, client.post("/api/shops/1/exports", headers=H, json={"scope": "platform"}).json())
    assert job["status"] == "failed"
    discovery_count = len([p for p, b in calls if p.endswith("latest_conversations")])
    assert client.get(f"/api/shops/1/exports/{job['id']}/download").status_code == 409
    behavior["fail"] = False
    assert client.post(f"/api/shops/1/exports/{job['id']}/resume", headers=H).status_code == 200
    job = wait_job(client, job)
    assert job["status"] == "done" and job["messages"] == 4
    assert len([p for p, b in calls if p.endswith("latest_conversations")]) == discovery_count


def test_delete_shop_disconnects_hides_and_restores_without_losing_transcripts(managed):
    client, app, _, _ = managed
    cid = add_local(app)
    assert (
        client.request("DELETE", "/api/shops/1", headers=H, json={"confirm_name": "wrong"}).status_code == 400
    )
    result = client.request("DELETE", "/api/shops/1", headers=H, json={"confirm_name": "Test shop"})
    assert result.status_code == 200, result.text
    assert client.get("/api/shops").json() == []
    assert client.get("/api/shops/deleted").json()[0]["id"] == 1
    assert app.state.store.one("SELECT cookies FROM shops WHERE id=1")["cookies"] == ""
    assert client.get(f"/api/conversations/{cid}").status_code == 404
    assert client.get("/api/history/conversations", params={"shop_id": 1}).status_code == 404
    assert client.post("/api/shops/1/connect", headers=H).status_code == 404
    assert app.state.store.one("SELECT count(*) n FROM messages")["n"] == 1
    assert client.post("/api/shops/1/restore", headers=H).status_code == 200
    assert client.get(f"/api/conversations/{cid}").status_code == 200
    assert client.get("/api/shops").json()[0]["id"] == 1
    assert client.get("/api/shops/deleted").json() == []


def test_delete_refuses_busy_or_exporting_shop(managed):
    client, app, _, _ = managed
    cid = add_local(app)
    app.state.store.execute("UPDATE messages SET state='sending' WHERE conversation_id=?", (cid,))
    result = client.request("DELETE", "/api/shops/1", headers=H, json={"confirm_name": "Test shop"})
    assert result.status_code == 409
    app.state.store.execute("UPDATE messages SET state='received'")

    async def waiting(*args):
        await asyncio.Event().wait()

    app.state.exports.history.conversations = waiting
    job = client.post("/api/shops/1/exports", headers=H, json={"scope": "platform"}).json()
    assert client.post("/api/shops/1/exports", headers=H, json={"scope": "local"}).json()["id"] == job["id"]
    assert (
        client.request("DELETE", "/api/shops/1", headers=H, json={"confirm_name": "Test shop"}).status_code
        == 409
    )
    assert client.post(f"/api/shops/1/exports/{job['id']}/cancel", headers=H).status_code == 200
    assert wait_job(client, job)["status"] == "cancelled"


def test_shop_management_requires_admin_and_checks_job_shop_ownership(managed):
    client, app, _, _ = managed
    job = wait_job(client, client.post("/api/shops/1/exports", headers=H, json={"scope": "local"}).json())
    second = client.post("/api/shops", headers=H, json={"name": "Second"}).json()["id"]
    assert client.get(f"/api/shops/{second}/exports/{job['id']}/download").status_code == 404
    app.state.store.execute(
        "INSERT INTO users(username,password,role,created_at) VALUES('agent',?,'agent',0)",
        (password_hash("agent-test-password"),),
    )
    client.post("/api/logout", headers=H)
    client.post("/api/login", headers=H, json={"username": "agent", "password": "agent-test-password"})
    assert client.post("/api/shops/1/exports", headers=H, json={"scope": "local"}).status_code == 403
    assert (
        client.request("DELETE", "/api/shops/1", headers=H, json={"confirm_name": "Test shop"}).status_code
        == 403
    )
    assert client.get("/api/shops/deleted").status_code == 403
    assert client.get(f"/api/shops/1/exports/{job['id']}/download").status_code == 403


def test_archived_shop_cannot_receive_even_if_an_old_channel_callback_arrives(managed):
    client, app, _, _ = managed
    client.request("DELETE", "/api/shops/1", headers=H, json={"confirm_name": "Test shop"})
    client.portal.call(
        app.state.channels.receive,
        1,
        {
            "uid": "late-buyer",
            "name": "Late buyer",
            "platform_id": "late",
            "content": "late",
            "goods_id": "",
            "automatic": False,
        },
    )
    assert app.state.store.one("SELECT count(*) n FROM conversations")["n"] == 0
    assert client.post("/api/shops", headers=H, json={"name": "New shop"}).json()["id"] != 1


def test_repeated_conversation_page_fails_explicitly_instead_of_looping(managed):
    client, app, _, _ = managed

    async def repeating(shop_id, page, size):
        return {"items": [{"buyer_uid": "buyer1", "buyer_name": "Buyer", "chat_type": ""}], "has_more": True}

    app.state.exports.history.conversations = repeating
    job = wait_job(client, client.post("/api/shops/1/exports", headers=H, json={"scope": "platform"}).json())
    assert job["status"] == "failed" and "分页" in job["error"]
