import json

import httpx
import pytest
from fastapi.testclient import TestClient

from desk.app import create_app

H = {"X-Desk-Request": "1"}


@pytest.fixture
def takeover(tmp_path):
    calls = []
    sent = []
    behavior = {
        "reply": {
            "answer": "您好，请问想了解哪款商品？",
            "needs_human": False,
            "source_ids": [],
            "reply_kind": "greeting",
            "reason": "问候",
        }
    }

    def model(request):
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(behavior["reply"], ensure_ascii=False)},
                    }
                ]
            },
        )

    def platform(request):
        assert request.url.path.endswith("send_message"), "Takeover test must not call unrelated endpoints"
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"success": True, "result": {}})

    app = create_app(tmp_path, httpx.MockTransport(model), httpx.MockTransport(platform))
    with TestClient(app) as client:
        client.post("/api/setup", headers=H, json={"username": "owner", "password": "takeover-password-123"})
        app.state.store.save_model(
            {"base_url": "https://models.test/v1", "model": "fixture", "use_local_keywords": True}
        )
        app.state.store.execute(
            "UPDATE shops SET cookies=? WHERE id=1",
            (app.state.store.encrypt(json.dumps({"fixture": "cookie"})),),
        )
        yield client, app, calls, sent, behavior


def receive(client, app, identity="m1", automatic=True):
    item = {
        "uid": "buyer",
        "name": "Buyer",
        "goods_id": "",
        "content": "你好",
        "platform_id": identity,
        "automatic": automatic,
    }
    client.portal.call(app.state.channels.receive, 1, item)
    return app.state.store.one("SELECT * FROM conversations WHERE buyer_uid='buyer'")


def activate(client):
    response = client.post("/api/shops/1/mode", headers=H, json={"mode": "ai_takeover"})
    assert response.status_code == 200, response.text


def test_mode_is_saved_and_ai_answers_greeting_even_with_keyword_filter_enabled(takeover):
    client, app, calls, sent, _ = takeover
    activate(client)
    receive(client, app)
    assert client.get("/api/shops").json()[0]["mode"] == "ai_takeover"
    assert len(calls) == len(sent) == 1
    assert json.loads(calls[0]["messages"][-1]["content"])["本地关键词筛选"] is False
    assert app.state.store.model()["use_local_keywords"] is True
    assert app.state.store.one("SELECT state FROM messages WHERE role='assistant'")["state"] == "sent"


@pytest.mark.parametrize("kind", ["clarification", "answer", "handoff"])
def test_only_verified_replies_are_sent_and_uncertain_facts_stay_as_drafts(takeover, kind):
    client, app, calls, sent, behavior = takeover
    behavior["reply"] = {
        "answer": "请问您需要哪种颜色？" if kind == "clarification" else "需要人工核实",
        "needs_human": kind == "handoff",
        "source_ids": [],
        "reply_kind": kind,
    }
    activate(client)
    conversation = receive(client, app)
    assert len(sent) == (1 if kind == "clarification" else 0)
    assert conversation["needs_human"] == (0 if kind == "clarification" else 1)


def test_human_takeover_and_recovered_history_never_auto_reply(takeover):
    client, app, calls, sent, _ = takeover
    activate(client)
    conversation = receive(client, app, automatic=False)
    assert not calls and not sent
    client.post(f"/api/conversations/{conversation['id']}/takeover", headers=H)
    receive(client, app, "m2")
    assert not calls and not sent


def test_switch_to_manual_while_model_is_generating_suppresses_reply(takeover):
    client, app, _, sent, _ = takeover
    activate(client)

    async def answer(*args, **kwargs):
        app.state.store.execute("UPDATE shops SET mode='manual' WHERE id=1")
        return {
            "answer": "Should not send",
            "needs_human": False,
            "sources": [{"id": 1}],
            "auto_reply_eligible": True,
        }

    app.state.knowledge.answer = answer
    receive(client, app)
    assert not sent and not app.state.store.one("SELECT id FROM messages WHERE role='draft'")


def test_existing_auto_mode_still_requires_sources_and_does_not_adopt_takeover_behavior(takeover):
    client, app, calls, sent, _ = takeover
    client.post("/api/shops/1/mode", headers=H, json={"mode": "auto"})
    receive(client, app)
    assert not calls and not sent


def test_takeover_mode_requires_a_configured_model(takeover):
    client, app, _, _, _ = takeover
    app.state.store.save_model({})
    response = client.post("/api/shops/1/mode", headers=H, json={"mode": "ai_takeover"})
    assert response.status_code == 409
    assert app.state.store.one("SELECT mode FROM shops WHERE id=1")["mode"] == "review"


def test_fact_answer_with_a_valid_product_source_is_sent(takeover):
    client, app, calls, sent, behavior = takeover
    store = app.state.store
    store.execute(
        "INSERT INTO documents(id,shop_id,title,goods_id,filename,sha256,created_by,created_at) VALUES('doc',1,'杯子','100','fixture.md','fixture',1,0)"
    )
    app.state.knowledge.publish("doc", "杯子", "100", "product", "杯子容量 500mL。")
    source = store.one("SELECT id FROM chunks WHERE document_id='doc'")["id"]
    behavior["reply"] = {
        "answer": "这款杯子是 500mL。",
        "needs_human": False,
        "source_ids": [source],
        "reply_kind": "answer",
    }
    activate(client)
    receive(client, app)
    assert len(calls) == len(sent) == 1
    assert store.one("SELECT state FROM messages WHERE role='assistant'")["state"] == "sent"


@pytest.mark.parametrize(
    "kind,citations", [("greeting", [999999]), ("clarification", [999999]), (["greeting"], [])]
)
def test_fabricated_sources_or_bad_reply_kind_cannot_trigger_sending(takeover, kind, citations):
    client, app, _, sent, behavior = takeover
    behavior["reply"] = {
        "answer": "Fixture response",
        "needs_human": False,
        "source_ids": citations,
        "reply_kind": kind,
    }
    activate(client)
    receive(client, app)
    assert not sent
    assert app.state.store.one("SELECT id FROM messages WHERE role='draft'")


def test_mode_configuration_cannot_be_changed_by_an_agent(takeover):
    from desk.store import password_hash

    client, app, _, _, _ = takeover
    app.state.store.execute(
        "INSERT INTO users(username,password,role,created_at) VALUES('agent',?,'agent',0)",
        (password_hash("agent-password-123"),),
    )
    client.post("/api/logout", headers=H)
    client.post("/api/login", headers=H, json={"username": "agent", "password": "agent-password-123"})
    assert client.post("/api/shops/1/mode", headers=H, json={"mode": "ai_takeover"}).status_code == 403
