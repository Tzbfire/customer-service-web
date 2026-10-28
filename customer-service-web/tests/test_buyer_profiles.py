import asyncio
import json
from pathlib import Path

import httpx
import pytest

from desk.channels import Channels
from desk.history import HistoryService
from desk.pdd_protocol import buyer_nickname, decode_push
from desk.store import Store


def make_channels(tmp_path, handler):
    store = Store(Path(tmp_path))
    store.execute(
        "INSERT INTO shops(id,name,cookies,created_at) VALUES(1,'Shop',?,0)",
        (store.encrypt(json.dumps({"fixture": "cookie"})),),
    )
    return store, Channels(store, None, httpx.MockTransport(handler))


def test_embedded_names_match_the_buyer_even_when_staff_sent_last_message():
    message = {
        "from": {"role": "mall_cs", "uid": "staff"},
        "to": {"role": "user", "uid": "123"},
        "nickname": "Staff member",
        "user_info": {"uid": 123, "nickname": "买家昵称"},
    }
    assert buyer_nickname(message, "123") == "买家昵称"
    message["user_info"]["uid"] = "another-buyer"
    assert buyer_nickname(message, "123") == ""
    message["to"]["nickname"] = "Recipient buyer"
    assert buyer_nickname(message, "123") == "Recipient buyer"


def test_push_uses_embedded_profile_and_never_staff_name():
    push = {
        "response": "push",
        "message": {
            "from": {"role": "user", "uid": "123"},
            "type": 0,
            "content": "hello",
            "msg_id": "m1",
            "user_info": {"uid": 123, "nickname": "买家昵称"},
        },
    }
    assert decode_push(push)["name"] == "买家昵称"


@pytest.mark.asyncio
async def test_profile_requests_are_deduplicated_cached_and_shop_scoped(tmp_path):
    calls = []

    async def platform(request):
        calls.append(json.loads(request.content))
        await asyncio.sleep(0.01)
        return httpx.Response(200, json={"success": True, "result": {"uid": 123, "nickname": "买家昵称"}})

    store, channels = make_channels(tmp_path, platform)
    cid = store.execute(
        "INSERT INTO conversations(shop_id,buyer_uid,buyer_name,updated_at) VALUES(1,'123','123',7)"
    )
    results = await asyncio.gather(*(channels.profiles.resolve(1, "123", "0") for _ in range(8)))
    assert results == ["买家昵称"] * 8 and len(calls) == 1
    assert calls[0] == {"uid": "123", "chatType": "0"}
    assert await channels.profiles.resolve(1, "123", "0") == "买家昵称"
    assert len(calls) == 1
    row = store.one("SELECT buyer_name,updated_at,mode FROM conversations WHERE id=?", (cid,))
    assert row == {"buyer_name": "买家昵称", "updated_at": 7, "mode": "ai"}
    store.execute(
        "INSERT INTO shops(id,name,cookies,created_at) VALUES(2,'Second',?,0)",
        (store.encrypt(json.dumps({"fixture": "other"})),),
    )
    assert channels.profiles.cached(2, "123", "0") is None
    await channels.profiles.resolve(2, "123", "0")
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_failures_and_mismatched_profiles_keep_names_and_back_off(tmp_path):
    calls = []

    def platform(request):
        calls.append(request.url.path)
        return httpx.Response(
            200, json={"success": True, "result": {"uid": "someone-else", "nickname": "Wrong"}}
        )

    store, channels = make_channels(tmp_path, platform)
    assert await channels.profiles.resolve(1, "123", fallback="Known buyer") == "Known buyer"
    assert await channels.profiles.resolve(1, "123", fallback="Known buyer") == "Known buyer"
    assert len(calls) == 1
    channels.transport = httpx.MockTransport(lambda r: httpx.Response(503))
    assert await channels.profiles.resolve(1, "456") == "456"
    assert await channels.profiles.resolve(1, "456") == "456"
    assert store.one("SELECT COUNT(*) n FROM buyer_profiles")["n"] == 2


@pytest.mark.asyncio
async def test_nameless_incoming_message_does_not_erase_existing_name(tmp_path):
    store, channels = make_channels(tmp_path, lambda r: httpx.Response(500))
    store.execute(
        "INSERT INTO conversations(shop_id,buyer_uid,buyer_name,updated_at) VALUES(1,'123','Known buyer',0)"
    )
    await channels.receive(
        1,
        {
            "uid": "123",
            "name": "123",
            "goods_id": "",
            "content": "hello",
            "platform_id": "m1",
            "automatic": False,
        },
    )
    assert store.one("SELECT buyer_name FROM conversations")["buyer_name"] == "Known buyer"


@pytest.mark.asyncio
async def test_history_names_from_embedded_profile_need_no_extra_request(tmp_path):
    calls = []

    def platform(request):
        calls.append(request.url.path)
        assert request.url.path == "/plateau/chat/latest_conversations"
        return httpx.Response(
            200,
            json={
                "success": True,
                "result": {
                    "conversations": [
                        {
                            "from": {"role": "mall_cs", "uid": "staff"},
                            "to": {"role": "user", "uid": "123"},
                            "nickname": "Staff member",
                            "user_info": {"uid": 123, "nickname": "买家昵称"},
                            "type": 0,
                            "msg_id": "m1",
                            "content": "reply",
                        }
                    ],
                    "has_more": False,
                },
            },
        )

    store, channels = make_channels(tmp_path, platform)
    result = await HistoryService(store, channels).conversations(1)
    assert result["items"][0]["buyer_name"] == "买家昵称"
    assert len(calls) == 1
    assert store.one("SELECT COUNT(*) n FROM conversations")["n"] == 0
    assert store.one("SELECT COUNT(*) n FROM messages")["n"] == 0


@pytest.mark.asyncio
async def test_large_inbox_advances_to_remaining_uncached_buyers(tmp_path):
    calls = []

    def platform(request):
        uid = json.loads(request.content)["uid"]
        calls.append(uid)
        return httpx.Response(200, json={"success": True, "result": {"uid": uid, "nickname": "Buyer " + uid}})

    _, channels = make_channels(tmp_path, platform)
    items = [{"buyer_uid": str(i), "buyer_name": str(i)} for i in range(25)]
    await channels.profiles.enrich(1, items)
    assert len(calls) == 20
    await channels.profiles.enrich(1, items)
    assert len(calls) == 25
    assert all(item["buyer_name"] == "Buyer " + item["buyer_uid"] for item in items)


@pytest.mark.asyncio
async def test_push_refreshes_cached_name_without_changing_reception(tmp_path):
    store, channels = make_channels(tmp_path, lambda r: httpx.Response(500))
    channels.profiles.remember(1, "123", "", "Old buyer")
    await channels.receive(
        1,
        {
            "uid": "123",
            "name": "New buyer",
            "goods_id": "",
            "content": "hello",
            "platform_id": "m1",
            "automatic": False,
        },
    )
    assert await channels.profiles.resolve(1, "123") == "New buyer"
    assert store.one("SELECT buyer_name FROM conversations")["buyer_name"] == "New buyer"
