import json

import pytest

from desk.knowledge import Knowledge
from desk.store import Store


class RecordingModel:
    def __init__(self):
        self.calls = []

    async def complete(self, messages):
        self.calls.append(messages)
        evidence = json.loads(messages[-1]["content"])["店铺资料"]
        return json.dumps(
            {
                "answer": "这是结合前文的商品介绍。" if evidence else "请提供具体商品，我帮您核对。",
                "needs_human": not bool(evidence),
                "source_ids": [x["id"] for x in evidence],
                "reason": "结合商品与上下文",
            },
            ensure_ascii=False,
        )


def system(tmp_path):
    store = Store(tmp_path)
    store.execute(
        "INSERT INTO users(id,username,password,role,created_at) VALUES(1,'owner','fixture','admin',0)"
    )
    store.execute("INSERT INTO shops(id,name,created_at) VALUES(1,'Shop',0)")
    model = RecordingModel()
    knowledge = Knowledge(store, model)
    for goods_id, content in [
        ("785427074097", "生态稻草漆。规格：75kg，颜色：米白色。"),
        ("822131120065", "另一款稻草泥。规格：20kg，颜色：灰色。"),
    ]:
        identifier = "doc" + goods_id
        store.execute(
            "INSERT INTO documents(id,shop_id,title,goods_id,filename,sha256,created_by,created_at) VALUES(?,1,?,?,?, ?,1,0)",
            (identifier, "商品 " + goods_id, goods_id, "fixture.md", identifier),
        )
        knowledge.publish(identifier, "商品 " + goods_id, goods_id, "product", content)
    return store, knowledge, model


@pytest.mark.asyncio
async def test_generic_followup_keeps_the_product_identified_in_previous_user_turn(tmp_path):
    _, knowledge, model = system(tmp_path)
    answer = await knowledge.answer(
        1,
        "详细介绍一下",
        history=[
            {"role": "user", "content": "785427074097"},
            {"role": "assistant", "content": "这是生态稻草漆。"},
        ],
    )
    assert model.calls, "Follow-up must reach the model instead of a keyword miss response"
    assert answer["sources"] and all(s["goods_id"] == "785427074097" for s in answer["sources"])
    assert answer["needs_human"] is False


@pytest.mark.asyncio
async def test_no_keyword_match_still_calls_model_to_understand_request(tmp_path):
    store, knowledge, model = system(tmp_path)
    store.save_model({"use_local_keywords": False})
    store.execute("UPDATE documents SET state='draft'")
    answer = await knowledge.answer(1, "你好，想问点事")
    assert model.calls, "A keyword miss must not decide whether the model participates"
    assert answer["sources"] == [] and answer["needs_human"] is True


@pytest.mark.asyncio
async def test_keyword_switch_on_keeps_the_explicit_local_filter_behavior(tmp_path):
    store, knowledge, model = system(tmp_path)
    store.save_model({"use_local_keywords": True})
    answer = await knowledge.answer(1, "你好，想问点事")
    assert not model.calls and answer["retrieval_mode"] == "keywords" and answer["model_called"] is False


@pytest.mark.asyncio
async def test_switch_off_does_not_call_keyword_search_and_scopes_context(tmp_path):
    store, knowledge, model = system(tmp_path)
    store.save_model({"use_local_keywords": False})

    def forbidden(*args, **kwargs):
        raise AssertionError("Keyword matching must be skipped entirely")

    knowledge.search = forbidden
    answer = await knowledge.answer(1, "详细介绍一下", goods_id="785427074097")
    assert model.calls and answer["model_called"] is True and answer["retrieval_mode"] == "model_context"
    evidence = json.loads(model.calls[0][-1]["content"])["店铺资料"]
    assert evidence and all(x["商品ID"] == "785427074097" for x in evidence)


@pytest.mark.asyncio
async def test_current_product_id_overrides_previous_product(tmp_path):
    _, knowledge, model = system(tmp_path)
    answer = await knowledge.answer(
        1, "改看 822131120065", goods_id="785427074097", history=[{"role": "user", "content": "785427074097"}]
    )
    assert answer["goods_id"] == "822131120065" and all(
        s["goods_id"] == "822131120065" for s in answer["sources"]
    )


def test_model_setting_switch_persists_and_older_clients_do_not_reset_it(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from desk.app import create_app

    monkeypatch.setattr("desk.app.validate_endpoint", lambda value, allow_private=False: value)
    app = create_app(tmp_path)
    headers = {"X-Desk-Request": "1"}
    with TestClient(app) as client:
        client.post(
            "/api/setup", headers=headers, json={"username": "owner", "password": "test-password-123"}
        )
        app.state.store.save_model(
            {"base_url": "https://models.test/v1", "model": "fixture", "api_key": "test-secret"}
        )
        assert client.get("/api/model").json()["use_local_keywords"] is True
        body = {"base_url": "https://models.test/v1", "model": "fixture", "use_local_keywords": False}
        assert client.post("/api/model", headers=headers, json=body).status_code == 200
        assert client.get("/api/model").json()["use_local_keywords"] is False
        assert app.state.store.model()["api_key"] == "test-secret"
        body.pop("use_local_keywords")
        assert client.post("/api/model", headers=headers, json=body).status_code == 200
        assert client.get("/api/model").json()["use_local_keywords"] is False
        body["use_local_keywords"] = True
        client.post("/api/model", headers=headers, json=body)
        assert app.state.store.model()["use_local_keywords"] is True


def test_model_context_excludes_unpublished_documents_and_reports_budget(tmp_path):
    store, knowledge, _ = system(tmp_path)
    rows, truncated = knowledge.model_context(1, "785427074097")
    assert rows and not truncated and all(row["goods_id"] == "785427074097" for row in rows)
    rows, truncated = knowledge.model_context(1, "785427074097", budget=1)
    assert rows == [] and truncated
    store.execute("UPDATE documents SET state='draft' WHERE goods_id='785427074097'")
    assert knowledge.model_context(1, "785427074097")[0] == []
