import asyncio
import json

import httpx
import pytest

from desk.pdd_protocol import PddProtocol, ProtocolError, auth_verdict, decode_frame, extract_build
from desk.store import Store


def test_build_is_discovered_from_live_asset_paths_and_missing_build_is_rejected():
    html = '<script src="https://mms-static.pinduoduo.com/chat-merchant/chat-merchant-v20261026.15.54.13/static/js/index.bundle.js"></script>'
    assert extract_build(html) == "202610261554"
    with pytest.raises(ProtocolError):
        extract_build("<html>login</html>")


def test_actual_auth_envelope_requires_consistent_success_and_detects_revocation():
    assert auth_verdict({"response": "auth", "auth": {"result": "ok"}, "result": "ok", "status": 1}) is True
    assert (
        auth_verdict({"response": "auth", "auth": {"result": "ok"}, "result": "fail", "status": 1}) is False
    )
    assert auth_verdict({"response": "auth", "auth": {"result": "ok"}, "status": 0}) is False
    assert auth_verdict({"response": "push", "message": {}}) is None
    with pytest.raises(ProtocolError):
        decode_frame(b"\x00\x01protobuf")
    with pytest.raises(ProtocolError):
        decode_frame("[]")


@pytest.mark.asyncio
async def test_readonly_probe_authenticates_current_build_and_cleans_up(tmp_path):
    requests = []

    def http(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200, text='<script src="/chat-merchant-v20261026.15.54.13/static/js/index.js"></script>'
            )
        if request.url.path.endswith("getToken"):
            assert json.loads(request.content) == {"version": 3}
            return httpx.Response(200, json={"token": "secret-not-for-output", "mall_status": 1})
        raise AssertionError(request.url.path)

    class Socket:
        closed = False
        sent = []

        def __init__(self):
            self.queue = asyncio.Queue()
            self.queue.put_nowait(
                json.dumps({"response": "auth", "auth": {"result": "ok"}, "result": "ok", "status": 1})
            )

        async def recv(self):
            return await self.queue.get()

        async def send(self, value):
            command = json.loads(value)
            self.sent.append(command)
            await self.queue.put(
                json.dumps({"response": "heartbeat", "result": "ok", "request_id": command["request_id"]})
            )

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            self.closed = True

    socket = Socket()

    def connector(url, **kwargs):
        assert "version=202610261554" in url
        assert kwargs["proxy"] is None
        return socket

    protocol = PddProtocol(Store(tmp_path), transport=httpx.MockTransport(http), connector=connector)
    result = await protocol.probe(1, {"test": "cookie"})
    assert (
        result["authenticated"] is True and result["heartbeat"] == "ok" and result["build"] == "202610261554"
    )
    assert result["transport"] == "legacy-json" and socket.closed
    assert [command["cmd"] for command in socket.sent] == ["heartbeat"]
    assert "secret-not-for-output" not in json.dumps(result)


@pytest.mark.asyncio
async def test_sync_commits_cursor_only_after_durable_delivery_and_recovers_manually(tmp_path):
    store = Store(tmp_path)
    store.execute("INSERT INTO shops(id,name,created_at) VALUES(1,?,0)", ("test",))
    calls = []

    def http(request):
        if request.url.path.endswith("get_init_state"):
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "result": {"init_sync_value": [{"seq_type": 1, "seq_id": 100}], "server_time": 1},
                },
            )
        body = json.loads(request.content)
        calls.append(body)
        cursor = body["sync_key"][0]["seq_id"]
        message = {
            "msg_id": "m-one",
            "from": {"role": "user", "uid": "buyer"},
            "to": {"role": "mall_cs", "uid": "staff"},
            "type": 0,
            "content": "test-only-question",
            "ts": 1,
        }
        return httpx.Response(
            200,
            json={
                "success": True,
                "result": {
                    "sync_data": [
                        {
                            "seq_type": 1,
                            "seq_id": cursor + 1,
                            "has_more": False,
                            "has_gap": False,
                            "data": [{"message": message, "chat_type_id": 1}],
                        }
                    ]
                },
            },
        )

    protocol = PddProtocol(store, transport=httpx.MockTransport(http))
    await protocol.initialize_sync(1, {"test": "cookie"})
    delivered = []

    async def fail(item):
        raise RuntimeError("storage unavailable")

    with pytest.raises(RuntimeError):
        await protocol.sync_once(1, {"test": "cookie"}, fail)
    assert protocol.cursors(1) == {1: 100}

    async def receive(item):
        delivered.append(item)

    await protocol.sync_once(1, {"test": "cookie"}, receive)
    assert protocol.cursors(1) == {1: 101}
    assert delivered[0]["automatic"] is False and delivered[0]["platform_id"] == "m-one"
    assert all(list(body) == ["sync_key"] for body in calls)


@pytest.mark.asyncio
async def test_heartbeat_timeout_and_expired_login_are_clear_and_bounded(tmp_path):
    def http(request):
        return httpx.Response(
            200, json={"success": False, "errorCode": 43001, "errorMsg": "private diagnostic"}
        )

    protocol = PddProtocol(Store(tmp_path), transport=httpx.MockTransport(http))
    with pytest.raises(ProtocolError, match="登录已过期"):
        await protocol.request({"test": "cookie"}, "/chats/getToken", {"version": 3})

    class Socket:
        async def recv(self):
            await asyncio.Event().wait()

    protocol.auth_timeout = 0.01
    with pytest.raises(ProtocolError, match="鉴权超时"):
        await protocol.authenticate(Socket())


@pytest.mark.asyncio
async def test_identity_success_code_1000000_is_not_mistaken_for_an_error(tmp_path):
    def http(request):
        return httpx.Response(
            200, json={"success": True, "errorCode": 1000000, "result": {"id": "fixture-only"}}
        )

    protocol = PddProtocol(Store(tmp_path), transport=httpx.MockTransport(http))
    result = await protocol.request({"fixture": "cookie"}, "/janus/api/new/userinfo", {})
    assert result["success"] is True


@pytest.mark.asyncio
async def test_full_receiver_uses_auth_heartbeat_and_sync_with_no_business_send(tmp_path):
    from desk.channels import Channels

    store = Store(tmp_path)
    store.execute(
        "INSERT INTO shops(id,name,cookies,mode,created_at) VALUES(1,?,?,'manual',0)",
        ("test", store.encrypt(json.dumps({"fixture": "cookie"}))),
    )

    def http(request):
        if request.method == "GET":
            return httpx.Response(
                200, text='<script src="/chat-merchant-v20261026.15.54.13/static/js/index.js"></script>'
            )
        path = request.url.path
        if path.endswith("getToken"):
            return httpx.Response(200, json={"token": "test-token"})
        if path.endswith("get_init_state"):
            return httpx.Response(
                200, json={"success": True, "result": {"init_sync_value": [{"seq_type": 1, "seq_id": 100}]}}
            )
        if path.endswith("/sync/message"):
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "result": {"sync_data": [{"seq_type": 1, "seq_id": 100, "data": [], "has_more": False}]},
                },
            )
        raise AssertionError("Unexpected business request")

    class Socket:
        closed = False

        def __init__(self):
            self.sent = []
            self.queue = asyncio.Queue()
            self.queue.put_nowait(
                json.dumps({"response": "auth", "auth": {"result": "ok"}, "result": "ok", "status": 1})
            )
            self.queue.put_nowait(
                json.dumps(
                    {
                        "response": "push",
                        "message": {
                            "from": {"role": "user", "uid": "buyer"},
                            "type": "0",
                            "msg_id": "live-fixture",
                            "content": "fixture only",
                        },
                    }
                )
            )

        async def recv(self):
            return await self.queue.get()

        async def send(self, raw):
            frame = json.loads(raw)
            self.sent.append(frame)
            assert frame["cmd"] == "heartbeat"
            await self.queue.put(
                json.dumps({"response": "heartbeat", "request_id": frame["request_id"], "result": "ok"})
            )

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            self.closed = True

    socket = Socket()
    channels = Channels(store, None, transport=httpx.MockTransport(http))
    channels.protocol.connector = lambda *args, **kwargs: socket
    task = asyncio.create_task(channels._run_connection(1))
    try:
        for _ in range(100):
            await asyncio.sleep(0.005)
            if task.done():
                task.result()
            if (
                channels.states.get(1, {}).get("last_heartbeat")
                and channels.states[1].get("sync") == "ok"
                and store.one("SELECT id FROM messages")
            ):
                break
        assert channels.states[1]["status"] == "connected"
        assert channels.states[1]["build"] == "202610261554"
        assert store.one("SELECT content FROM messages")["content"] == "fixture only"
        assert channels.states[1]["outgoing_validation"] == "not_tested"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert socket.closed and all(frame["cmd"] == "heartbeat" for frame in socket.sent)


@pytest.mark.asyncio
async def test_sync_gap_never_advances_the_saved_checkpoint(tmp_path):
    store = Store(tmp_path)
    store.execute("INSERT INTO shops(id,name,created_at) VALUES(1,?,0)", ("test",))
    store.execute("INSERT INTO pdd_sync(shop_id,seq_type,seq_id,updated_at) VALUES(1,1,100,0)")

    def http(request):
        return httpx.Response(
            200,
            json={
                "success": True,
                "result": {
                    "sync_data": [
                        {"seq_type": 1, "seq_id": 200, "has_gap": True, "data": [], "has_more": False}
                    ]
                },
            },
        )

    protocol = PddProtocol(store, transport=httpx.MockTransport(http))

    async def receive(item):
        raise AssertionError("must not deliver a gap response")

    with pytest.raises(ProtocolError, match="缺口"):
        await protocol.sync_once(1, {"fixture": "cookie"}, receive)
    assert protocol.cursors(1) == {1: 100}


def test_numeric_string_message_types_and_malformed_frames_do_not_crash():
    from desk.pdd_protocol import decode_push

    assert decode_push({"response": "push", "message": {"from": None}}) is None
    item = decode_push(
        {
            "response": "push",
            "message": {
                "from": {"role": "user", "uid": "buyer"},
                "type": "0",
                "sub_type": "2",
                "msg_id": "m",
                "content": "card",
                "conv_id": "conv1",
                "chat_type": "normal",
            },
        }
    )
    assert item["automatic"] is False and item["platform_conv_id"] == "conv1"
    assert (
        decode_push({"response": "push", "message": {"from": {"role": "user", "uid": "buyer"}, "type": "20"}})
        is None
    )
