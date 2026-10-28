import json

import httpx
import pytest
from fastapi.testclient import TestClient

from desk.app import create_app
from desk.channels import Channels
from desk.history import HistoryService
from desk.message_content import media_url, normalize_message, read_presentation, message_preview
from desk.pdd_protocol import decode_push
from desk.store import Store


def raw(kind=0, **values):
    return {
        "type": kind,
        "msg_id": "m1",
        "from": {"role": "user", "uid": "buyer"},
        "to": {"role": "mall_cs", "uid": "staff"},
        "ts": 1700000000000,
        **values,
    }


def product_info():
    return {
        "goodsID": 123,
        "goodsName": "Sample product",
        "goodsPrice": "95",
        "couponPromoPrice": "87",
        "goodsThumbUrl": "https://img.pddpic.com/item.jpg",
        "linkUrl": "https://mobile.yangkeduo.com/goods.html?goods_id=123",
    }


def test_live_and_history_keep_same_image_metadata_and_timestamp():
    message = raw(1, content="https://img.pddpic.com/photo.jpg")
    history = normalize_message(message)
    live = decode_push({"response": "push", "message": message})
    assert live["presentation"] == history["presentation"]
    assert history["created_at"] == 1700000000
    assert history["presentation"]["media"][0]["inline_url"] == message["content"]
    assert live["automatic"] is False


def test_product_inquiry_is_a_structured_card_and_prices_are_not_divided_twice():
    result = normalize_message(
        raw(info=product_info(), content="https://mobile.yangkeduo.com/goods.html?goods_id=123")
    )
    card = result["presentation"]
    assert card["kind"] == "product" and card["body"] == ""
    assert card["product"]["price"] == "¥95.00" and card["product"]["coupon_price"] == "¥87.00"
    assert card["product"]["image_url"].endswith("item.jpg")
    assert "goods.html" not in result["content"]


def test_order_cards_accept_both_platform_layouts_and_do_not_invent_missing_details():
    flat = normalize_message(
        raw(
            sub_type=1,
            info={
                **product_info(),
                "orderSequenceNo": "ORDER1",
                "spec": "Large",
                "afterSalesStatus": "退款中",
            },
        )
    )
    nested = normalize_message(
        raw(8, info={"goods_info": {"goods_id": 123, "goods_name": "Sample", "order_sequence_no": "ORDER2"}})
    )
    assert flat["presentation"]["order"]["id"] == "ORDER1"
    assert flat["presentation"]["order"]["spec"] == "Large"
    assert nested["presentation"]["order"]["id"] == "ORDER2"
    assert nested["presentation"]["product"]["price"] == ""
    assert nested["presentation"]["product"]["image_url"] is None


def test_nested_spec_card_preserves_goods_scope():
    message = normalize_message(
        raw(64, info={"data": {"goods_info": {"goods_id": 456, "goods_name": "Coating", "spec": "Gray"}}})
    )
    assert message["goods_id"] == "456" and message["presentation"]["product"]["spec"] == "Gray"


def test_video_emotion_faq_and_unknown_messages_remain_readable():
    assert (
        normalize_message(raw(14, content="https://video.pddpic.com/test.mp4"))["presentation"]["media"][0][
            "kind"
        ]
        == "video"
    )
    assert normalize_message(raw(5, info={"description": "[玫瑰]"}))["content"] == "[玫瑰]"
    faq = normalize_message(
        raw(
            56,
            content="menu",
            info={
                "content": [
                    {"type": "menu_item", "text": "Where from?", "click_action": {"method": "danger"}}
                ]
            },
        )
    )
    assert faq["role"] == "system" and faq["presentation"]["body"] == "Where from?"
    assert "click_action" not in json.dumps(faq)
    other = normalize_message(raw(12345, content="Readable fallback"))
    assert other["presentation"]["kind"] == "unsupported" and "Readable fallback" in other["content"]


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "https://img.pddpic.com.evil.test/x",
        "http://127.0.0.1/a",
        "https://user:pass@img.pddpic.com/x",
        "https://img.pddpic.com:444/x",
        "https://evil.test/x",
    ],
)
def test_untrusted_media_never_gets_an_inline_source(url):
    assert media_url(url) is None
    result = normalize_message(raw(1, content=url))
    assert all(not m["inline_url"] for m in result["attachments"])


def make_channel(tmp_path, handler=None):
    store = Store(tmp_path)
    store.execute(
        "INSERT INTO shops(id,name,cookies,created_at) VALUES(1,'Shop',?,0)",
        (store.encrypt(json.dumps({"fixture": "test"})),),
    )
    return store, Channels(store, None, httpx.MockTransport(handler or (lambda r: httpx.Response(500))))


@pytest.mark.asyncio
async def test_inbound_staff_media_is_saved_without_requesting_ai_or_marking_human_needed(tmp_path):
    store, channel = make_channel(tmp_path)
    item = decode_push(
        {
            "response": "push",
            "message": raw(
                1,
                content="https://img.pddpic.com/photo.jpg",
                **{"from": {"role": "mall_cs", "uid": "staff"}, "to": {"role": "user", "uid": "buyer"}},
            ),
        }
    )
    await channel.receive(1, item)
    row = store.one("SELECT * FROM messages")
    assert row["role"] == "agent" and read_presentation(row["presentation"])["kind"] == "image"
    assert store.one("SELECT needs_human FROM conversations")["needs_human"] == 0


@pytest.mark.asyncio
async def test_recall_with_same_platform_id_hides_original_without_duplicate_or_losing_content(tmp_path):
    store, channel = make_channel(tmp_path)
    incoming = decode_push({"response": "push", "message": raw(content="Original question")})
    incoming["automatic"] = False
    await channel.receive(1, incoming)
    await channel.receive(
        1,
        decode_push(
            {
                "response": "push",
                "message": raw(1002, info={"update_type": 3, "msg_id": "m1", "withdraw_hint": "消息已撤回"}),
            }
        ),
    )
    rows = store.all("SELECT * FROM messages")
    assert len(rows) == 1 and rows[0]["content"] == "Original question"
    assert read_presentation(rows[0]["presentation"])["kind"] == "withdrawn"


@pytest.mark.asyncio
async def test_out_of_order_recall_prevents_original_from_triggering_model(tmp_path):
    store, channel = make_channel(tmp_path)
    await channel.receive(
        1,
        decode_push(
            {
                "response": "push",
                "message": raw(1002, msg_id="recall", info={"update_type": 3, "msg_id": "m1"}),
            }
        ),
    )
    await channel.receive(1, decode_push({"response": "push", "message": raw(content="Withdrawn question")}))
    row = store.one("SELECT * FROM messages WHERE platform_id='m1'")
    assert read_presentation(row["presentation"])["kind"] == "withdrawn"


@pytest.mark.asyncio
async def test_legacy_backfill_updates_only_presentation_and_keeps_mode_message_count_and_text(tmp_path):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        assert request.url.path == "/plateau/chat/list"
        return httpx.Response(
            200, json={"success": True, "result": {"messages": [raw(info=product_info())], "has_more": False}}
        )

    store, channel = make_channel(tmp_path, handler)
    cid = store.execute(
        "INSERT INTO conversations(shop_id,buyer_uid,buyer_name,mode,updated_at) VALUES(1,'buyer','Buyer','human',0)"
    )
    store.execute(
        "INSERT INTO messages(conversation_id,role,content,platform_id,created_at) VALUES(?,'customer','【商品】old text','m1',0)",
        (cid,),
    )
    conversation = store.one("SELECT * FROM conversations WHERE id=?", (cid,))
    messages = store.all("SELECT * FROM messages")
    await HistoryService(store, channel).enrich_local_messages(conversation, messages)
    assert len(calls) == 1 and len(store.all("SELECT * FROM messages")) == 1
    assert store.one("SELECT mode FROM conversations")["mode"] == "human"
    assert (
        messages[0]["content"] == "【商品】old text" and messages[0]["presentation"]["product"]["id"] == "123"
    )


def test_local_api_serves_persisted_media_and_csp_permits_only_platform_media(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        client.post(
            "/api/setup",
            headers={"X-Desk-Request": "1"},
            json={"username": "owner", "password": "test-only-password"},
        )
        app.state.channels.profiles.remember(1, "buyer", "", "Buyer")
        item = decode_push(
            {"response": "push", "message": raw(1, content="https://img.pddpic.com/photo.jpg")}
        )
        client.portal.call(app.state.channels.receive, 1, item)
        cid = app.state.store.one("SELECT id FROM conversations")["id"]
        response = client.get(f"/api/conversations/{cid}")
        assert response.json()["messages"][0]["presentation"]["media"][0]["inline_url"].endswith("photo.jpg")
        csp = response.headers["Content-Security-Policy"]
        assert "https://*.pddpic.com" in csp and "img-src *" not in csp and "media-src 'self'" in csp


def test_conversation_preview_has_no_tracking_url_and_summarizes_cards():
    assert (
        message_preview("【商品 123：Sample】\nhttps://mobile.yangkeduo.com/goods.html?token=secret")
        == "【商品 123：Sample】"
    )
    assert message_preview("old", {"kind": "image"}) == "【图片】"
    assert message_preview("old", {"product": {"name": "Sample"}}) == "【商品】Sample"
