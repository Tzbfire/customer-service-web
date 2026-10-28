import io
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from desk.app import create_app
from desk.products import normalize_product, knowledge_text
from desk.store import password_hash

H = {"X-Desk-Request": "1"}


@pytest.fixture
def catalog(tmp_path):
    calls = []
    model_calls = []
    behavior = {"removed": False, "wrong_mall": False, "image_fail": False}
    image = io.BytesIO()
    Image.new("RGB", (30, 30), "white").save(image, "PNG")

    def platform(request):
        calls.append(request.url.path)
        if request.method == "GET":
            if behavior["image_fail"]:
                return httpx.Response(503)
            return httpx.Response(200, content=image.getvalue(), headers={"content-type": "image/png"})
        payload = json.loads(request.content)
        if request.url.path.endswith("recommendGoods"):
            page = payload["pageNum"]
            goods_id = "100" if page == 1 else "200"
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "result": {
                        "onSaleGoods": [
                            {
                                "goodsId": goods_id,
                                "goodsName": "稻草漆 " + goods_id,
                                "thumbUrl": "https://img.pddpic.com/item.jpg",
                                "minOnSaleGroupPrice": 9500,
                                "maxOnSaleGroupPrice": 10000,
                            }
                        ],
                        "total": 1 if behavior["removed"] else 2,
                    },
                },
            )
        if request.url.path.endswith("/on_shop/detail"):
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "result": {
                        "goods_id": payload["goods_id"],
                        "mall_id": "wrong" if behavior["wrong_mall"] else "mall1",
                        "goods_name": "稻草漆 " + payload["goods_id"],
                        "goods_desc": "墙面需平整牢固，不可掉粉。",
                        "cats": ["建材", "涂料"],
                        "skus": [
                            {
                                "sku_id": "sku1",
                                "spec": [{"parent_name": "颜色", "spec_name": "米白色"}],
                                "multi_price": 9500,
                                "is_onsale": 1,
                            }
                        ],
                        "detail_gallery": [{"url": "https://img.pddpic.com/detail.jpg"}],
                    },
                },
            )
        raise AssertionError("Product sync may only call catalog endpoints or images")

    def model(request):
        payload = json.loads(request.content)
        model_calls.append(payload)
        user = payload["messages"][-1]["content"]
        if isinstance(user, list):
            response = "商品资料：每桶可施工 20 平方米。"
        else:
            evidence = json.loads(user)["店铺资料"]
            response = json.dumps(
                {
                    "answer": "提供米白色规格，墙面需要平整牢固。",
                    "needs_human": False,
                    "source_ids": [evidence[0]["id"]],
                    "reason": "平台商品资料",
                },
                ensure_ascii=False,
            )
        return httpx.Response(
            200, json={"choices": [{"finish_reason": "stop", "message": {"content": response}}]}
        )

    app = create_app(
        tmp_path, model_transport=httpx.MockTransport(model), channel_transport=httpx.MockTransport(platform)
    )
    with TestClient(app) as client:
        client.post("/api/setup", headers=H, json={"username": "owner", "password": "catalog-test-password"})
        app.state.store.execute(
            "UPDATE shops SET cookies=?,platform_shop_id='mall1' WHERE id=1",
            (app.state.store.encrypt(json.dumps({"fixture": "cookie"})),),
        )
        app.state.store.save_model(
            {"base_url": "https://models.test/v1", "model": "fixture", "vision_enabled": True}
        )
        yield client, app, calls, model_calls, behavior


def wait_sync(client):
    for _ in range(150):
        result = client.get("/api/products?shop_id=1").json()
        if result["sync"] and result["sync"]["status"] != "running":
            return result
        time.sleep(0.01)
    raise AssertionError("Catalog sync did not finish")


def sync(client):
    response = client.post("/api/shops/1/products/sync", headers=H)
    assert response.status_code == 200, response.text
    return wait_sync(client)


def wait_images(client, goods_id):
    for _ in range(150):
        item = client.get(f"/api/shops/1/products/{goods_id}").json()
        if item["image_state"] != "running":
            return item
        time.sleep(0.01)
    raise AssertionError("OCR did not finish")


def test_product_sync_fetches_all_pages_and_publishes_product_scoped_knowledge(catalog):
    client, app, calls, models, _ = catalog
    result = sync(client)
    assert result["sync"]["status"] == "done" and result["sync"]["completed"] == 2
    assert len(result["items"]) == 2 and all(p["knowledge_state"] == "ready" for p in result["items"])
    assert calls.count("/latitude/goods/recommendGoods") == 2 and len(models) == 0
    sources = app.state.knowledge.search(1, "米白色规格", "100")
    assert sources and all(s["goods_id"] == "100" for s in sources)
    assert "¥95.00" in sources[0]["content"] and "米白色" in sources[0]["content"]
    response = client.post(
        "/api/playground", headers=H, json={"shop_id": 1, "question": "有米白色规格吗？", "goods_id": "100"}
    )
    assert response.status_code == 200 and response.json()["sources"][0]["goods_id"] == "100"
    assert all(s["goods_id"] == "100" for s in response.json()["sources"])


def test_repeat_sync_does_not_duplicate_documents_or_overwrite_human_edits(catalog):
    client, app, _, _, _ = catalog
    sync(client)
    sync(client)
    assert app.state.store.one("SELECT count(*) n FROM documents")["n"] == 2
    product = app.state.store.one("SELECT document_id FROM products WHERE goods_id='100'")
    app.state.knowledge.publish(
        product["document_id"], "人工维护", "100", "product", "人工修订的米白色涂料资料。"
    )
    result = sync(client)
    row = next(p for p in result["items"] if p["goods_id"] == "100")
    assert "人工编辑" in row["error"]
    assert (
        app.state.store.one("SELECT content FROM documents WHERE id=?", (product["document_id"],))["content"]
        == "人工修订的米白色涂料资料。"
    )


def test_mall_identity_mismatch_is_not_imported(catalog):
    client, app, _, _, behavior = catalog
    behavior["wrong_mall"] = True
    result = sync(client)
    assert result["sync"]["status"] == "partial" and result["sync"]["failed"] == 2
    assert app.state.store.one("SELECT count(*) n FROM documents")["n"] == 0
    assert all(p["state"] == "failed" for p in result["items"])


def test_missing_saleable_product_is_disabled_after_complete_enumeration(catalog):
    client, app, _, _, behavior = catalog
    sync(client)
    behavior["removed"] = True
    sync(client)
    gone = app.state.store.one("SELECT document_id,available FROM products WHERE goods_id='200'")
    assert gone["available"] == 0
    assert (
        app.state.store.one("SELECT state FROM documents WHERE id=?", (gone["document_id"],))["state"]
        == "draft"
    )
    assert not app.state.knowledge.search(1, "米白色", "200")


def test_detail_images_are_reviewed_before_becoming_knowledge_and_retry_uses_cache(catalog):
    client, app, _, models, _ = catalog
    sync(client)
    assert client.post("/api/shops/1/products/100/images", headers=H).status_code == 200
    result = wait_images(client, "100")
    assert result["image_state"] == "draft" and result["image_document_id"]
    doc = app.state.store.one("SELECT * FROM documents WHERE id=?", (result["image_document_id"],))
    assert doc["state"] == "draft" and "20 平方米" in doc["content"]
    assert "data:image/jpeg;base64," in str(models[0])
    assert not any(s["document_id"] == doc["id"] for s in app.state.knowledge.search(1, "施工面积", "100"))
    client.post(
        f"/api/documents/{doc['id']}/publish",
        headers=H,
        json={"title": doc["title"], "goods_id": "100", "category": "product", "content": doc["content"]},
    )
    assert any(s["document_id"] == doc["id"] for s in app.state.knowledge.search(1, "施工面积", "100"))
    count = len(models)
    client.post("/api/shops/1/products/100/images", headers=H)
    wait_images(client, "100")
    assert len(models) == count
    assert app.state.store.one("SELECT count(*) n FROM documents")["n"] == 3
    assert app.state.store.one("SELECT state FROM documents WHERE id=?", (doc["id"],))["state"] == "ready"


def test_product_sync_requires_admin_and_valid_shop(catalog):
    client, app, _, _, _ = catalog
    assert client.post("/api/shops/99/products/sync", headers=H).status_code == 404
    assert client.get("/api/products?shop_id=99").status_code == 404
    app.state.store.execute(
        "INSERT INTO users(username,password,role,created_at) VALUES('agent',?,'agent',0)",
        (password_hash("agent-test-password"),),
    )
    client.post("/api/logout", headers=H)
    client.post("/api/login", headers=H, json={"username": "agent", "password": "agent-test-password"})
    assert client.post("/api/shops/1/products/sync", headers=H).status_code == 403
    assert client.post("/api/shops/1/products/100/images", headers=H).status_code == 403
    assert client.get("/api/products?shop_id=1").status_code == 200


def test_normalization_does_not_turn_missing_fields_into_claims():
    product = normalize_product({"goodsId": "123", "goodsName": "A coating", "minOnSaleGroupPrice": 0})
    content = knowledge_text(product)
    assert product["price_min"] == "¥0.00" and product["description"] == ""
    assert product["skus"] == [] and product["detail_images"] == []
    assert "防水" not in content and "包邮" not in content


def test_failed_image_identifies_its_position_and_keeps_previous_cache(catalog):
    client, app, _, _, behavior = catalog
    sync(client)
    behavior["image_fail"] = True
    client.post("/api/shops/1/products/100/images", headers=H)
    product = wait_images(client, "100")
    assert product["image_state"] == "failed" and "第 1/1 张" in product["image_error"]
