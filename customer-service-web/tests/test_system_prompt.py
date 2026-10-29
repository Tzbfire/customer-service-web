from desk.store import Store
from test_ai_takeover import activate, receive, takeover as takeover
from test_workflows import H, publish, system as system, upload


PROMPT = "你是专业涂料客服。先确认墙面条件和施工面积，回复不要用亲。\n测试特殊文本 </textarea><script>example</script>"


def save(client, **overrides):
    values = {"base_url": "http://127.0.0.1:12345/v1", "model": "text-test", "allow_private": True}
    values.update(overrides)
    return client.post("/api/model", headers=H, json=values)


def test_system_prompt_roundtrip_preserves_key_and_survives_restart(system):
    client, app, _, _ = system
    assert client.get("/api/model").json()["system_prompt"] == ""
    assert save(client, system_prompt=PROMPT).status_code == 200
    settings = client.get("/api/model").json()
    assert settings["system_prompt"] == PROMPT and settings["has_key"] is True
    assert "api_key" not in settings
    assert Store(app.state.store.directory).model()["system_prompt"] == PROMPT
    assert app.state.store.model()["api_key"] == "test-only-secret"
    # An older client omitting the new field must not erase it.
    assert save(client).status_code == 200
    assert client.get("/api/model").json()["system_prompt"] == PROMPT
    assert save(client, system_prompt="").status_code == 200
    assert client.get("/api/model").json()["system_prompt"] == ""


def test_prompt_length_validation_does_not_overwrite_existing_prompt(system):
    client, _, _, _ = system
    assert save(client, system_prompt=PROMPT).status_code == 200
    assert save(client, system_prompt="长" * 12001).status_code == 422
    assert client.get("/api/model").json()["system_prompt"] == PROMPT


def test_playground_applies_business_prompt_and_keeps_existing_response_contract(system):
    client, _, calls, _ = system
    publish(client, upload(client))
    assert save(client, system_prompt=PROMPT).status_code == 200
    result = client.post(
        "/api/playground", headers=H, json={"shop_id": 1, "goods_id": "100", "question": "杯子容量？"}
    )
    assert result.status_code == 200, result.text
    messages = calls[-1]["messages"]
    assert messages[0]["role"] == "system" and PROMPT in messages[0]["content"]
    assert "只输出 JSON" in messages[0]["content"] and "source_ids" in messages[0]["content"]
    assert result.json()["sources"]
    assert save(client, system_prompt="").status_code == 200
    client.post(
        "/api/playground", headers=H, json={"shop_id": 1, "goods_id": "100", "question": "杯子容量？"}
    )
    assert PROMPT not in calls[-1]["messages"][0]["content"]


def test_live_ai_takeover_receives_saved_system_prompt(takeover):
    client, app, calls, sent, _ = takeover
    assert save(client, system_prompt=PROMPT).status_code == 200
    activate(client)
    receive(client, app)
    assert len(calls) == len(sent) == 1
    assert PROMPT in calls[0]["messages"][0]["content"]
    assert "reply_kind" in calls[0]["messages"][0]["content"]


def test_ocr_and_connection_check_do_not_use_customer_service_prompt(system):
    import base64
    import io

    from PIL import Image

    client, app, calls, _ = system
    assert save(client, system_prompt=PROMPT, vision_enabled=True).status_code == 200
    image = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(image, "PNG")
    client.portal.call(app.state.knowledge.model.transcribe, base64.b64encode(image.getvalue()).decode())
    assert PROMPT not in calls[-1]["messages"][0]["content"]
    assert "转录" in calls[-1]["messages"][0]["content"]
    assert client.post("/api/model/test", headers=H).status_code == 200
    assert calls[-1]["messages"] == [{"role": "user", "content": "请只回复 OK"}]
