import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from desk.app import create_app
from desk.learning import purchase_facts, scoped_messages
from desk.store import password_hash

H = {"X-Desk-Request": "1"}


@pytest.fixture
def learning_system(tmp_path):
    calls = []
    models = []
    behavior = {
        "wrong_owner": False,
        "bad_area": False,
        "bad_evidence": False,
        "model_fails": False,
        "history_fails": False,
    }

    def order(sn, when, quantity=1):
        return {
            "uid": "buyer1234",
            "mallId": "mall1",
            "orderSn": sn,
            "orderTime": when,
            "payTime": when,
            "orderStatus": 1,
            "payStatus": 2,
            "groupStatus": 1,
            "orderGoodsList": {
                "goodsId": "100",
                "skuId": "sku1",
                "goodsName": "Coating",
                "spec": "75kg 米白",
                "goodsNumber": quantity,
            },
        }

    def platform(request):
        calls.append((request.url.path, json.loads(request.content)))
        if request.url.path.endswith("userAllOrder"):
            body = {
                "total": 1,
                "historyTotal": 1,
                "historyTotalPeriodCode": 2,
                "orders": [order("new-secret-order", 250, 2)],
            }
            if behavior["wrong_owner"]:
                body["orders"][0]["uid"] = "another-buyer"
        elif request.url.path.endswith("userHistoryOrder"):
            if behavior["history_fails"]:
                return httpx.Response(503)
            body = {
                "total": 1,
                "orders": [
                    {
                        "uid": "buyer1234",
                        "mall_id": "mall1",
                        "order_sn": "old-secret-order",
                        "created_at": 50,
                        "order_status": 1,
                        "pay_status": 2,
                        "group_status": 1,
                        "goods_id": "100",
                        "sku_id": "sku1",
                        "goods_name": "Coating",
                        "spec": "75kg 米白",
                        "goods_number": 1,
                    }
                ],
            }
        else:
            raise AssertionError("Learning may only read buyer orders")
        return httpx.Response(200, json={"success": True, "result": body})

    def model(request):
        payload = json.loads(request.content)
        models.append(payload)
        if behavior["model_fails"]:
            return httpx.Response(503)
        cases = json.loads(payload["messages"][-1]["content"])["cases"]
        summaries = []
        faq = []
        for case in cases:
            area = next(m for m in case["messages"] if m["role"] == "customer" and "平方" in m["content"])
            answer = next(m for m in case["messages"] if m["role"] == "agent")
            summaries.append(
                {
                    "case_id": case["case_id"],
                    "discussion": "询问面积所需数量，客服先核实墙面。",
                    "area_message_ref": area["ref"],
                    "area_quote": "999 平方米" if behavior["bad_area"] else "30 平方米",
                }
            )
            faq.append(
                {
                    "question": "施工前要核实什么？",
                    "answer": "先核实墙面是否平整牢固。",
                    "case_ids": [case["case_id"]],
                    "evidence": [
                        {
                            "case_id": case["case_id"],
                            "message_ref": answer["ref"],
                            "quote": "不存在的客服话" if behavior["bad_evidence"] else answer["content"],
                        }
                    ],
                }
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {"cases": summaries, "faq": faq, "limitations": ["数量不能换算通用覆盖率"]},
                                ensure_ascii=False,
                            )
                        },
                    }
                ]
            },
        )

    app = create_app(tmp_path, httpx.MockTransport(model), httpx.MockTransport(platform))
    with TestClient(app) as client:
        client.post("/api/setup", headers=H, json={"username": "owner", "password": "test-password-123"})
        store = app.state.store
        store.execute(
            "UPDATE shops SET platform_shop_id='mall1',cookies=? WHERE id=1",
            (store.encrypt(json.dumps({"fixture": "cookie"})),),
        )
        store.save_model({"base_url": "https://models.test/v1", "model": "fixture"})
        store.execute(
            "INSERT INTO products(shop_id,goods_id,name,seen_run,updated_at) VALUES(1,'100','Coating','fixture',0)"
        )
        store.execute(
            "INSERT INTO exports(id,shop_id,scope,status,phase,conversations,completed,messages,created_at,updated_at) VALUES('snapshot',1,'platform','done','done',1,1,4,0,0)"
        )
        db = app.state.exports.connect("snapshot")
        messages = [
            {
                "id": 1,
                "platform_id": "m1",
                "role": "customer",
                "content": "商品链接",
                "created_at": 100,
                "presentation": {"product": {"id": "100"}, "body": "咨询商品100"},
            },
            {
                "id": 2,
                "platform_id": "m2",
                "role": "customer",
                "content": "30 平方米，需要多少？电话13812345678，订单new-secret-order",
                "created_at": 200,
                "presentation": {},
            },
            {
                "id": 3,
                "platform_id": "m3",
                "role": "agent",
                "content": "先核实墙面是否平整牢固。",
                "created_at": 210,
                "presentation": {},
            },
        ]
        db.execute("INSERT INTO contacts(uid,chat_type,name) VALUES('buyer1234','','Buyer')")
        app.state.exports.add_messages(db, "buyer1234", "", messages, "platform")
        db.commit()
        db.close()
        yield client, app, calls, models, behavior


def wait_job(client, job):
    for _ in range(250):
        current = client.get(f"/api/shops/1/learning/{job['id']}").json()
        if current["job"]["status"] != "running":
            return current
        time.sleep(0.01)
    raise AssertionError("Learning did not finish")


def start(client):
    response = client.post("/api/shops/1/products/100/learning", headers=H)
    assert response.status_code == 200, response.text
    return wait_job(client, response.json())


def test_recent_and_older_orders_are_both_read_and_normalized(learning_system):
    client, app, calls, models, _ = learning_system
    result = client.get("/api/shops/1/buyers/buyer1234/orders")
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["complete"] and len(data["orders"]) == 2 and data["history_total"] == 1
    assert [o["quantity"] for o in data["orders"]] == [1, 2]
    assert all(o["paid_purchase"] for o in data["orders"]) and not models
    assert len(calls) == 2


def test_learning_runs_order_chat_model_review_publish_flow_without_sending(learning_system):
    client, app, calls, models, _ = learning_system
    preview = client.get("/api/shops/1/products/100/learning-preview").json()
    assert preview["cases"] == 1 and not models
    result = start(client)
    job = result["job"]
    assert job["status"] == "done", job
    case = result["cases"][0]
    assert case["analysis"]["area_quote"] == "30 平方米"
    assert case["purchases"]["repeat_ever"] is True
    assert (
        case["purchases"]["paid_after_consultation"] == 1
        and case["purchases"]["repeat_after_consultation"] is False
    )
    assert case["purchases"]["consultation_at"] == 200
    sent = json.dumps(models, ensure_ascii=False)
    assert "buyer1234" not in sent and "new-secret-order" not in sent and "13812345678" not in sent
    doc = app.state.store.one("SELECT * FROM documents WHERE id=?", (job["document_id"],))
    assert doc["state"] == "draft" and "30 平方米" in doc["content"] and "C0001/M3" in doc["content"]
    assert "new-secret-order" not in doc["content"] and "buyer1234" not in doc["content"]
    assert not app.state.knowledge.search(1, "墙面", "100")
    response = client.post(
        f"/api/documents/{doc['id']}/publish",
        headers=H,
        json={"title": doc["title"], "content": doc["content"], "goods_id": "100", "category": "product"},
    )
    assert response.status_code == 200 and app.state.knowledge.search(1, "墙面", "100")
    assert app.state.store.one("SELECT count(*) n FROM messages")["n"] == 0
    assert app.state.store.one("SELECT count(*) n FROM conversations")["n"] == 0


@pytest.mark.parametrize("key", ["bad_area", "bad_evidence"])
def test_invented_model_evidence_is_rejected_before_knowledge_creation(learning_system, key):
    client, app, _, _, behavior = learning_system
    behavior[key] = True
    result = start(client)
    assert result["job"]["status"] == "failed" and not result["job"]["document_id"]
    assert app.state.store.one("SELECT count(*) n FROM documents")["n"] == 0


def test_incomplete_orders_remain_unknown_instead_of_becoming_no_purchase(learning_system):
    client, app, _, _, behavior = learning_system
    behavior["history_fails"] = True
    result = start(client)
    assert result["job"]["status"] == "partial"
    assert result["cases"][0]["purchases"]["known"] is False
    content = app.state.store.one(
        "SELECT content FROM documents WHERE id=?", (result["job"]["document_id"],)
    )["content"]
    assert "购买与复购：未知" in content


def test_failed_model_can_resume_without_refetching_orders(learning_system):
    client, app, calls, models, behavior = learning_system
    behavior["model_fails"] = True
    result = start(client)
    job = result["job"]
    assert job["status"] == "failed"
    count = len(calls)
    behavior["model_fails"] = False
    assert client.post(f"/api/shops/1/learning/{job['id']}/resume", headers=H).status_code == 200
    result = wait_job(client, job)
    assert result["job"]["status"] == "done" and len(calls) == count


def test_other_buyer_or_mall_orders_are_rejected(learning_system):
    client, app, _, _, behavior = learning_system
    behavior["wrong_owner"] = True
    response = client.get("/api/shops/1/buyers/buyer1234/orders")
    assert response.status_code == 502 and "归属" in response.json()["detail"]


def test_learning_requires_admin_and_matching_shop_job(learning_system):
    client, app, _, _, _ = learning_system
    result = start(client)
    job = result["job"]
    client.post("/api/shops", headers=H, json={"name": "Second"})
    assert client.get(f"/api/shops/2/learning/{job['id']}").status_code == 404
    app.state.store.execute(
        "INSERT INTO users(username,password,role,created_at) VALUES('agent',?,'agent',0)",
        (password_hash("agent-password-123"),),
    )
    client.post("/api/logout", headers=H)
    client.post("/api/login", headers=H, json={"username": "agent", "password": "agent-password-123"})
    assert client.post("/api/shops/1/products/100/learning", headers=H).status_code == 403
    assert client.get(f"/api/shops/1/learning/{job['id']}").status_code == 403


def test_product_switch_stops_attributing_other_product_chat():
    rows = [
        {"role": "customer", "content": "A", "created_at": 1, "presentation": {"product": {"id": "100"}}},
        {"role": "agent", "content": "A answer", "created_at": 2, "presentation": {}},
        {"role": "customer", "content": "B", "created_at": 3, "presentation": {"product": {"id": "200"}}},
        {"role": "agent", "content": "B answer", "created_at": 4, "presentation": {}},
    ]
    messages, truncated = scoped_messages(rows, "100")
    assert [m["content"] for m in messages] == ["A", "A answer"] and not truncated


def test_purchase_quantity_is_not_repeat_count_and_missing_times_are_unknown():
    order = {
        "goods_id": "100",
        "paid_purchase": True,
        "ordered_at": 0,
        "quantity": 5,
        "spec": "75kg",
        "sku_id": "s1",
        "pay_status": 2,
        "order_status": 1,
        "source": "recent",
    }
    facts = purchase_facts(
        {"orders": {"complete": True, "orders": [order], "scope": "test"}, "messages": [{"at": 100}]}, "100"
    )
    assert facts["repeat_ever"] is False and facts["quantities_by_spec"] == {"75kg": 5}
    assert facts["paid_after_consultation"] is None


def test_learning_without_export_or_product_association_fails_clearly(learning_system):
    client, app, _, models, _ = learning_system
    app.state.store.execute("UPDATE exports SET status='failed' WHERE id='snapshot'")
    response = client.post("/api/shops/1/products/100/learning", headers=H)
    assert response.status_code == 502 and "历史对话导出" in response.json()["detail"]
    assert not models


def test_paid_refunded_cancelled_or_unknown_orders_are_not_counted_as_successful_purchases():
    from desk.orders import normalize_order

    raw = {
        "uid": "b",
        "mallId": "m",
        "orderSn": "o",
        "orderGoodsList": {"goodsId": "100", "goodsNumber": 5},
        "orderStatus": 1,
        "payStatus": 4,
        "groupStatus": 1,
    }
    assert normalize_order(raw, "b", "m", "recent")["paid_purchase"] is False
    raw["payStatus"] = 2
    raw["orderStatus"] = 2
    assert normalize_order(raw, "b", "m", "recent")["paid_purchase"] is False
    raw["orderStatus"] = None
    assert normalize_order(raw, "b", "m", "recent")["paid_purchase"] is False


def test_order_history_pagination_continues_until_total_and_deduplicates(learning_system):
    client, app, _, _, _ = learning_system
    pages = []

    async def platform(cookies, path, body):
        pages.append((path, body["pageNo"]))
        old = path.endswith("userHistoryOrder")
        page = body["pageNo"]
        common = {
            "uid": "buyer1234",
            "mallId": "mall1",
            "orderStatus": 1,
            "payStatus": 2,
            "groupStatus": 1,
            "orderGoodsList": {"goodsId": "100", "goodsNumber": 1},
        }
        if not old:
            return {
                "result": {
                    "total": 1,
                    "historyTotal": 2,
                    "historyTotalPeriodCode": 2,
                    "orders": [{**common, "orderSn": "same", "orderTime": 200}],
                }
            }
        return {
            "result": {
                "total": 2,
                "orders": [
                    {
                        **common,
                        "orderSn": "same" if page == 1 else "older",
                        "orderTime": 100 if page == 1 else 50,
                    }
                ],
            }
        }

    app.state.learning.orders.channels.api = platform
    response = client.get("/api/shops/1/buyers/buyer1234/orders")
    assert response.status_code == 200, response.text
    assert len(response.json()["orders"]) == 2
    assert pages == [
        ("/latitude/order/userAllOrder", 1),
        ("/latitude/order/userHistoryOrder", 1),
        ("/latitude/order/userHistoryOrder", 2),
    ]
    assert next(o for o in response.json()["orders"] if o["order_sn"] == "same")["source"] == "recent"
