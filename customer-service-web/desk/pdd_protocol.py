"""Version discovery and the live-verified PDD JSON channel with HTTP sequence recovery.

Titan binary transport is explicitly unsupported here; it is never silently treated as JSON.
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
from contextlib import asynccontextmanager
from time import monotonic, time
from urllib.parse import urlencode

import httpx
import websockets

from .message_content import normalize_message

ORIGIN = "https://mms.pinduoduo.com"
WORKSTATION = ORIGIN + "/chat-merchant/index.html"
SOCKET_ORIGIN = "wss://m-ws.pinduoduo.com/"


class ProtocolError(Exception):
    """Safe diagnostics containing no token, URL query, cookies or customer content."""


def extract_build(html):
    direct = re.search(r"(?:window\.)?SERVICE_BUILD_TIME\s*=\s*[\"\']?(\d{12,14})", html)
    if direct:
        return direct.group(1)
    builds = re.findall(r"chat-merchant-v(\d{8})\.(\d{2})\.(\d{2})\.\d{2}", html)
    if builds:
        return max("".join(parts) for parts in builds)
    raise ProtocolError("未发现当前工作台版本，请检查登录状态，稍后重新连接。")


def decode_frame(raw):
    if not isinstance(raw, str):
        raise ProtocolError("收到非 JSON 的二进制帧，当前通道不能解析；需要核对平台接入分支。")
    try:
        frame = json.loads(raw)
    except (ValueError, TypeError):
        raise ProtocolError("平台返回了无法识别的消息帧。") from None
    if not isinstance(frame, dict):
        raise ProtocolError("平台消息帧不是 JSON 对象。")
    return frame


def auth_verdict(frame):
    if frame.get("response") != "auth":
        return None
    nested = frame.get("auth")
    nested = nested.get("result") if isinstance(nested, dict) else None
    verdicts = [value for value in (nested, frame.get("result")) if value is not None]
    return (
        bool(verdicts)
        and all(value in ("ok", "success") for value in verdicts)
        and frame.get("status", 1) not in (0, "0", False)
    )


def integer(value, label="序号"):
    if isinstance(value, bool):
        raise ProtocolError(f"平台{label}格式不正确。")
    try:
        result = int(value)
    except (ValueError, TypeError, OverflowError):
        raise ProtocolError(f"平台{label}格式不正确。") from None
    if result < 0 or result > 2**63 - 1:
        raise ProtocolError(f"平台{label}超出有效范围。")
    return result


def valid_buyer_name(value, uid):
    if not isinstance(value, str):
        return ""
    value = value.strip()[:200]
    return value if value and value != str(uid) else ""


def buyer_nickname(message, uid):
    """Only use identities bound to this buyer; a staff nickname is never a buyer name."""
    for key in ("from", "to"):
        actor = message.get(key)
        if isinstance(actor, dict) and actor.get("role") == "user":
            identity = actor.get("uid", actor.get("id"))
            if str(identity) == str(uid):
                name = valid_buyer_name(actor.get("nickname"), uid)
                if name:
                    return name
    profile = message.get("user_info")
    if isinstance(profile, dict):
        identity = profile.get("uid", profile.get("id"))
        if identity is None or str(identity) == str(uid):
            name = valid_buyer_name(profile.get("nickname"), uid)
            if name:
                return name
    sender = message.get("from")
    if isinstance(sender, dict) and sender.get("role") == "user":
        identity = sender.get("uid", sender.get("id"))
        if str(identity) == str(uid):
            return valid_buyer_name(message.get("nickname"), uid)
    return ""


def decode_push(data):
    if not isinstance(data, dict) or data.get("response") != "push":
        return None
    message = data.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("from"), dict):
        return None
    sender = message["from"]
    recipient = message.get("to")
    recipient = recipient if isinstance(recipient, dict) else {}
    buyer = (
        sender
        if sender.get("role") == "user"
        else recipient
        if (sender.get("role") in {"mall_cs", "mall", "system"} and recipient.get("role") == "user")
        else {}
    )
    uid = buyer.get("uid", buyer.get("id"))
    if uid is None or str(uid) == "":
        return None
    normalized = normalize_message(message)
    kind = normalized["type"]
    if kind == 20:
        return None  # Read synchronization contains no visible message.
    subtype = str(message.get("sub_type"))
    automatic = sender.get("role") == "user" and kind == 0 and subtype not in {"1", "2"}
    return {
        "uid": str(uid),
        "name": buyer_nickname(message, uid) or str(uid),
        "content": normalized["content"][:12000],
        "goods_id": normalized["goods_id"],
        "platform_id": normalized["platform_id"],
        "automatic": automatic and normalized["platform_id"] is not None,
        "role": normalized["role"],
        "presentation": normalized["presentation"],
        "platform_conv_id": normalized["platform_conv_id"],
        "chat_type": normalized["chat_type"],
        "timestamp": message.get("ts") or message.get("time"),
    }


class PddProtocol:
    auth_timeout = 12
    heartbeat_interval = 25
    heartbeat_timeout = 12
    sync_interval = 15
    max_sync_pages = 50

    def __init__(self, store, transport=None, connector=None):
        self.store = store
        self.transport = transport
        self.connector = connector or websockets.connect
        self.build_cache = None
        self.build_time = 0
        self.build_lock = asyncio.Lock()
        self.sync_locks = {}

    async def request(self, cookies, path, body=None, *, method="POST", form=False):
        if not path.startswith("/") or path.startswith("//"):
            raise ProtocolError("平台接口路径不正确。")
        try:
            async with httpx.AsyncClient(
                cookies=cookies,
                timeout=25,
                follow_redirects=False,
                transport=self.transport,
                trust_env=False,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
                    "Referer": WORKSTATION,
                    "Origin": ORIGIN,
                },
            ) as client:
                if method == "GET":
                    response = await client.get(ORIGIN + path)
                else:
                    params = (
                        {"data": body if body is not None else {}}
                        if form
                        else {"json": body if body is not None else {}}
                    )
                    response = await client.post(ORIGIN + path, **params)
                if 300 <= response.status_code < 400:
                    raise ProtocolError("平台要求重新登录，请打开浏览器登录。")
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError):
            raise ProtocolError("平台请求未能确认结果，请检查网络和登录状态。") from None
        if not isinstance(data, dict):
            raise ProtocolError("平台响应格式不正确。")
        code = data.get("error_code") or data.get("errorCode")
        if str(code) == "43001":
            raise ProtocolError("拼多多登录已过期，请重新连接店铺。")
        if str(code) in {"54001", "10002"}:
            raise ProtocolError(f"平台要求额外验证（代码 {code}），请在浏览器中完成验证后重新连接。")
        success_code = str(code) == "1000000" and data.get("success") is True
        if data.get("success") is False or (code not in (None, 0, "0", "") and not success_code):
            number = str(code) if str(code).isdigit() else "unknown"
            raise ProtocolError(f"平台拒绝请求（代码 {number}），请检查权限或登录状态。")
        return data

    async def discover_build(self, cookies, force=False):
        async with self.build_lock:
            if not force and self.build_cache and monotonic() - self.build_time < 3600:
                return self.build_cache
            try:
                async with httpx.AsyncClient(
                    cookies=cookies,
                    timeout=20,
                    transport=self.transport,
                    follow_redirects=False,
                    trust_env=False,
                ) as client:
                    response = await client.get(WORKSTATION)
                if response.status_code != 200:
                    raise ProtocolError("无法读取当前工作台版本，请重新登录或稍后再试。")
                build = extract_build(response.text)
            except httpx.HTTPError:
                raise ProtocolError("读取工作台版本失败，请检查网络。") from None
            self.build_cache, self.build_time = build, monotonic()
            return build

    @asynccontextmanager
    async def open(self, cookies):
        build = await self.discover_build(cookies)
        data = await self.request(cookies, "/chats/getToken", {"version": 3})
        result = data.get("result")
        token = data.get("token") or (result.get("token") if isinstance(result, dict) else None)
        if not isinstance(token, str) or not token:
            raise ProtocolError("未取得消息连接令牌，请重新登录店铺。")
        query = urlencode({"access_token": token, "role": "mall_cs", "client": "web", "version": build})
        try:
            async with self.connector(
                SOCKET_ORIGIN + "?" + query,
                open_timeout=15,
                close_timeout=5,
                ping_interval=25,
                ping_timeout=12,
                compression=None,
                max_size=10**7,
                proxy=None,
            ) as socket:
                auth = await self.authenticate(socket)
                yield (
                    socket,
                    {
                        "transport": "legacy-json",
                        "build": build,
                        "authenticated": True,
                        "auth_status": auth.get("status"),
                    },
                )
        except (OSError, websockets.exceptions.WebSocketException, TimeoutError):
            raise ProtocolError("消息连接中断或握手失败，正在等待重连。") from None

    async def authenticate(self, socket):
        try:
            async with asyncio.timeout(self.auth_timeout):
                while True:
                    frame = decode_frame(await socket.recv())
                    verdict = auth_verdict(frame)
                    if verdict is True:
                        return frame
                    if verdict is False:
                        raise ProtocolError("消息鉴权失败，请重新登录店铺。")
                    # Never discard a buyer message before auth and pretend the channel is complete.
                    if frame.get("response") == "push":
                        raise ProtocolError("鉴权前收到了业务消息，连接顺序异常。")
        except TimeoutError:
            raise ProtocolError("消息鉴权超时，连接尚未就绪。") from None

    async def probe(self, shop_id, cookies):
        async with self.open(cookies) as (socket, info):
            request_id = secrets.randbits(52)
            await socket.send(json.dumps({"cmd": "heartbeat", "request_id": request_id}))
            try:
                async with asyncio.timeout(self.heartbeat_timeout):
                    while True:
                        frame = decode_frame(await socket.recv())
                        if frame.get("response") == "heartbeat" and frame.get("request_id") == request_id:
                            if frame.get("result") != "ok":
                                raise ProtocolError("平台心跳未通过。")
                            return {**info, "heartbeat": "ok", "outgoing_validation": "not_tested"}
            except TimeoutError:
                raise ProtocolError("平台应用层心跳超时。") from None

    def cursors(self, shop_id):
        rows = self.store.all("SELECT seq_type,seq_id FROM pdd_sync WHERE shop_id=?", (shop_id,))
        return {row["seq_type"]: row["seq_id"] for row in rows}

    async def initialize_sync(self, shop_id, cookies):
        if self.cursors(shop_id):
            return
        data = await self.request(cookies, "/plateau/sync/get_init_state", {"client_type": "WEB"})
        result = data.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("init_sync_value"), list):
            raise ProtocolError("平台没有返回消息同步初始序号。")
        # Only type 1 (chat) is ingested. Type 10 is read/system state, not buyer messages.
        initial = [item for item in result["init_sync_value"] if str(item.get("seq_type")) == "1"]
        if not initial:
            raise ProtocolError("平台没有返回可识别的会话消息序号。")
        with self.store.connect() as db:
            for item in initial:
                db.execute(
                    "INSERT OR IGNORE INTO pdd_sync(shop_id,seq_type,seq_id,updated_at) VALUES(?,?,?,?)",
                    (shop_id, 1, integer(item.get("seq_id")), time()),
                )

    async def sync_once(self, shop_id, cookies, receive):
        lock = self.sync_locks.setdefault(shop_id, asyncio.Lock())
        async with lock:
            await self.initialize_sync(shop_id, cookies)
            recovered = 0
            for seq_type, start in self.cursors(shop_id).items():
                cursor = start
                for _ in range(self.max_sync_pages):
                    data = await self.request(
                        cookies,
                        "/plateau/sync/message",
                        {"sync_key": [{"seq_type": seq_type, "seq_id": cursor}]},
                    )
                    result = data.get("result")
                    groups = result.get("sync_data") if isinstance(result, dict) else None
                    if not isinstance(groups, list):
                        raise ProtocolError("消息同步响应缺少序号数据。")
                    matches = [group for group in groups if str(group.get("seq_type")) == str(seq_type)]
                    if len(matches) != 1:
                        raise ProtocolError("消息同步响应的序号类型不匹配。")
                    group = matches[0]
                    next_cursor = integer(group.get("seq_id"))
                    if group.get("has_gap"):
                        raise ProtocolError("平台提示历史消息同步存在缺口，需要在商家后台人工核对。")
                    if next_cursor < cursor:
                        raise ProtocolError("平台消息序号发生回退，需要核对账号状态。")
                    entries = group.get("data")
                    if not isinstance(entries, list):
                        raise ProtocolError("消息同步列表格式不正确。")
                    for entry in entries:
                        if not isinstance(entry, dict):
                            raise ProtocolError("消息同步条目格式不正确。")
                        message = entry.get("message") if "message" in entry else entry
                        item = decode_push({"response": "push", "message": message})
                        if item:
                            item["automatic"] = False
                            item["recovered"] = True
                            await receive(item)
                            recovered += 1
                    # Advance only after messages are committed by receive. Retries are deduplicated by msg_id.
                    self.store.execute(
                        "UPDATE pdd_sync SET seq_id=?,updated_at=? WHERE shop_id=? AND seq_type=?",
                        (next_cursor, time(), shop_id, seq_type),
                    )
                    if not group.get("has_more"):
                        break
                    if next_cursor == cursor:
                        raise ProtocolError("平台消息同步没有推进序号，已停止循环。")
                    cursor = next_cursor
                else:
                    raise ProtocolError("消息同步超过单轮分页上限，请人工核对历史咨询。")
            return recovered
