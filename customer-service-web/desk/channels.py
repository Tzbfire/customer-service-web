"""PDD protocol adapter, based on the checked-out Customer-Agent channel implementation.

No GUI imports: the web process owns its connections, review queue and reply state.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import sqlite3
from time import monotonic, time
from urllib.parse import urlsplit


from .buyer_profiles import BuyerProfiles
from .llm import ModelError
from .message_content import read_presentation, timestamp
from .pdd_protocol import (
    PddProtocol,
    ProtocolError,
    auth_verdict,
    decode_frame,
    decode_push,
    valid_buyer_name,
)


class ChannelError(ProtocolError):
    pass


def normalize_cookies(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            raise ValueError("登录态必须是浏览器导出的 Cookie JSON。") from None
    if isinstance(value, list):
        value = {
            item["name"]: item["value"]
            for item in value
            if isinstance(item, dict)
            and "name" in item
            and "value" in item
            and (
                not item.get("domain")
                or (
                    item["domain"].lstrip(".") == "pinduoduo.com" or item["domain"].endswith(".pinduoduo.com")
                )
            )
        }
    if not isinstance(value, dict) or not value or len(value) > 300:
        raise ValueError("登录态为空或格式不正确。")
    if any(
        not isinstance(k, str) or not isinstance(v, str) or "\n" in k + v or "\r" in k + v
        for k, v in value.items()
    ):
        raise ValueError("Cookie 名称和值必须是文字，不能包含换行。")
    return value


class Channels:
    def __init__(self, store, knowledge, transport=None):
        self.store = store
        self.knowledge = knowledge
        self.transport = transport
        self.protocol = PddProtocol(store, transport=transport)
        self.profiles = BuyerProfiles(store, self)
        self.tasks = {}
        self.states = {}
        self.locks = {}
        self.login_tasks = {}
        self.login_pages = {}
        self.login_states = {}

    def lock(self, conversation_id):
        return self.locks.setdefault(conversation_id, asyncio.Lock())

    async def api(self, cookies, path, body=None, form=False):
        # Keep the sender transport override used by integration tests.
        self.protocol.transport = self.transport
        try:
            return await self.protocol.request(cookies, path, body, form=form)
        except ProtocolError as exc:
            raise ChannelError(str(exc)) from None

    def protocol_report(self, shop_id):
        row = self.store.one("SELECT value FROM settings WHERE key=?", (f"pdd.protocol:{shop_id}",))
        return json.loads(row["value"]) if row else None

    async def check_protocol(self, shop_id):
        task = self.tasks.get(shop_id)
        if task and not task.done():
            raise ChannelError("请先断开消息连接，再执行独立协议检查。")
        self.protocol.transport = self.transport
        try:
            report = await self.protocol.probe(shop_id, self.cookies(shop_id))
            data = await self.protocol.request(
                self.cookies(shop_id), "/plateau/sync/get_init_state", {"client_type": "WEB"}
            )
            result = data.get("result")
            state = result.get("init_sync_value") if isinstance(result, dict) else None
            if not isinstance(state, list) or not any(
                str(item.get("seq_type")) == "1" for item in state if isinstance(item, dict)
            ):
                raise ProtocolError("平台没有返回可识别的会话消息序号。")
            report.update(checked_at=time(), sync_endpoint="ok")
        except ProtocolError as exc:
            raise ChannelError(str(exc)) from None
        self.store.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (f"pdd.protocol:{shop_id}", json.dumps(report)),
        )
        return report

    async def bind(self, shop_id, cookies):
        cookies = normalize_cookies(cookies)
        user = await self.api(cookies, "/janus/api/new/userinfo", "")
        merchant = await self.api(cookies, "/earth/api/merchant/queryMerchantInfoByMallId")
        user = user.get("result", {})
        merchant = merchant.get("result", {})
        if not user.get("id") or not merchant.get("mallId"):
            raise ChannelError("未取得店铺身份，请确认使用商家后台有效登录态。")
        shop = self.store.one("SELECT * FROM shops WHERE id=?", (shop_id,))
        if not shop or shop.get("deleted_at"):
            raise ChannelError("店铺已删除，请先恢复店铺。")
        if shop.get("platform_shop_id") and shop["platform_shop_id"] != str(merchant["mallId"]):
            raise ChannelError("当前登录的店铺与原绑定店铺不同，请新建店铺后连接。")
        await self.stop(shop_id)
        current = self.store.one("SELECT deleted_at FROM shops WHERE id=?", (shop_id,))
        if not current or current["deleted_at"]:
            raise ChannelError("店铺已删除，请先恢复店铺。")
        if shop.get("platform_user_id") != str(user["id"]):
            self.store.execute("DELETE FROM pdd_sync WHERE shop_id=?", (shop_id,))
            self.store.execute("DELETE FROM settings WHERE key=?", (f"pdd.protocol:{shop_id}",))
        self.store.execute(
            "UPDATE shops SET name=?,platform_shop_id=?,platform_user_id=?,cookies=? WHERE id=?",
            (
                merchant.get("mallName") or shop["name"],
                str(merchant["mallId"]),
                str(user["id"]),
                self.store.encrypt(json.dumps(cookies)),
                shop_id,
            ),
        )
        self.states[shop_id] = {"status": "offline", "detail": "登录态已保存，可以连接消息服务。"}

    def cookies(self, shop_id):
        shop = self.store.one("SELECT cookies,deleted_at FROM shops WHERE id=?", (shop_id,))
        if not shop or shop.get("deleted_at") or not shop["cookies"]:
            raise ChannelError("请先登录并绑定拼多多店铺。")
        return normalize_cookies(self.store.decrypt(shop["cookies"]))

    async def start(self, shop_id):
        self.cookies(shop_id)
        if shop_id not in self.tasks or self.tasks[shop_id].done():
            self.states[shop_id] = {"status": "connecting", "detail": "正在连接平台消息服务。"}
            self.tasks[shop_id] = asyncio.create_task(self.run(shop_id))

    async def stop(self, shop_id):
        task = self.tasks.pop(shop_id, None)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.states[shop_id] = {"status": "offline", "detail": "消息连接已停止。"}

    async def run(self, shop_id):
        failures = 0
        while failures < 12:
            started = monotonic()
            try:
                await self._run_connection(shop_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if monotonic() - started > 120:
                    failures = 0
                failures += 1
                detail = (
                    str(exc) if isinstance(exc, ProtocolError) else "消息处理或连接异常，请检查平台登录状态。"
                )
                self.states[shop_id] = {"status": "reconnecting", "detail": detail, "attempt": failures}
                await asyncio.sleep(min(2 ** min(failures - 1, 6), 45))
        self.states[shop_id] = {"status": "failed", "detail": "多次重连失败，请运行协议检查或重新登录。"}

    async def _run_connection(self, shop_id):
        cookies = self.cookies(shop_id)
        self.protocol.transport = self.transport
        # Establish the baseline before opening the socket. Later polls recover the connection window too.
        await self.protocol.initialize_sync(shop_id, cookies)
        async with self.protocol.open(cookies) as (socket, connection):
            self.states[shop_id] = {
                "status": "connected",
                "detail": "鉴权已通过，正在接收买家消息。",
                **connection,
                "sync": "starting",
                "outgoing_validation": "not_tested",
            }
            queue = asyncio.Queue(maxsize=500)
            acknowledgements = {}

            async def receiver():
                while True:
                    frame = decode_frame(await socket.recv())
                    verdict = auth_verdict(frame)
                    if verdict is False:
                        raise ProtocolError("平台撤销了消息鉴权，请重新登录。")
                    if frame.get("response") == "heartbeat":
                        future = acknowledgements.get(frame.get("request_id"))
                        if future is not None and not future.done():
                            future.set_result(frame.get("result") == "ok")
                        continue
                    item = decode_push(frame)
                    if item:
                        try:
                            queue.put_nowait(item)
                        except asyncio.QueueFull:
                            raise ProtocolError("消息处理队列已满，将重新连接并通过序号同步恢复。") from None

            async def heartbeat():
                while True:
                    request_id = secrets.randbits(52)
                    future = asyncio.get_running_loop().create_future()
                    acknowledgements[request_id] = future
                    try:
                        await socket.send(json.dumps({"cmd": "heartbeat", "request_id": request_id}))
                        try:
                            accepted = await asyncio.wait_for(future, self.protocol.heartbeat_timeout)
                        except TimeoutError:
                            raise ProtocolError("平台应用层心跳超时，将重新连接。") from None
                        if not accepted:
                            raise ProtocolError("平台应用层心跳失败，将重新连接。")
                        self.states[shop_id]["last_heartbeat"] = time()
                    finally:
                        acknowledgements.pop(request_id, None)
                    await asyncio.sleep(self.protocol.heartbeat_interval)

            async def sync():
                async def recover(item):
                    await self.receive(shop_id, item)

                while True:
                    count = await self.protocol.sync_once(shop_id, cookies, recover)
                    state = self.states[shop_id]
                    state.update(sync="ok", last_sync=time(), recovered=state.get("recovered", 0) + count)
                    await asyncio.sleep(self.protocol.sync_interval)

            tasks = [
                asyncio.create_task(receiver()),
                asyncio.create_task(heartbeat()),
                asyncio.create_task(sync()),
            ]
            tasks.extend(asyncio.create_task(self.consume(shop_id, queue)) for _ in range(4))
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
                raise ProtocolError("消息连接意外结束。")
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    async def consume(self, shop_id, queue):
        while True:
            item = await queue.get()
            try:
                await self.receive(shop_id, item)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.states[shop_id] = {
                    "status": "connected",
                    "detail": "一条消息处理失败，请检查待人工会话。",
                }
            finally:
                queue.task_done()

    async def receive(self, shop_id, item):
        shop = self.store.one("SELECT deleted_at FROM shops WHERE id=?", (shop_id,))
        if not shop or shop["deleted_at"]:
            return
        role = item.get("role", "customer")
        presentation = item.get("presentation", {})
        with self.store.connect() as db:
            if presentation.get("withdraw_target"):
                changed = db.execute(
                    "UPDATE messages SET presentation=? WHERE conversation_id IN "
                    "(SELECT id FROM conversations WHERE shop_id=? AND buyer_uid=?) AND platform_id=?",
                    (
                        json.dumps({"version": 1, "kind": "withdrawn", "body": presentation["body"]}),
                        shop_id,
                        item["uid"],
                        presentation["withdraw_target"],
                    ),
                ).rowcount
                if changed:
                    return
            existing = (
                db.execute(
                    "SELECT m.id,m.state,m.created_at,m.presentation FROM messages m "
                    "JOIN conversations c ON c.id=m.conversation_id "
                    "WHERE c.shop_id=? AND c.buyer_uid=? AND m.platform_id=?",
                    (shop_id, item["uid"], item["platform_id"]),
                ).fetchone()
                if item["platform_id"] is not None
                else None
            )
            if existing:
                # History refresh may win the race with a live push; only that fresh live push
                # can claim the message for normal processing, once. Historical recovery cannot.
                fresh = abs(time() - existing["created_at"]) < 120
                can_claim = (
                    existing["state"] == "historical"
                    and role == "customer"
                    and item.get("automatic")
                    and not item.get("recovered")
                    and fresh
                    and read_presentation(existing["presentation"]).get("kind") != "withdrawn"
                )
                if (
                    not can_claim
                    or not db.execute(
                        "UPDATE messages SET state='received' WHERE id=? AND state='historical'",
                        (existing["id"],),
                    ).rowcount
                ):
                    return
            db.execute(
                "INSERT INTO conversations(shop_id,buyer_uid,buyer_name,goods_id,updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(shop_id,buyer_uid) DO UPDATE SET buyer_name=CASE WHEN excluded.buyer_name<>excluded.buyer_uid "
                "AND trim(excluded.buyer_name)<>'' THEN excluded.buyer_name ELSE conversations.buyer_name END,"
                "goods_id=CASE WHEN excluded.goods_id<>'' THEN excluded.goods_id ELSE conversations.goods_id END,"
                "updated_at=excluded.updated_at",
                (shop_id, item["uid"], item["name"], item["goods_id"], time()),
            )
            conversation = dict(
                db.execute(
                    "SELECT * FROM conversations WHERE shop_id=? AND buyer_uid=?", (shop_id, item["uid"])
                ).fetchone()
            )
            if item.get("platform_conv_id") or item.get("chat_type"):
                db.execute(
                    "UPDATE conversations SET platform_conv_id=CASE WHEN ?<>'' THEN ? ELSE platform_conv_id END, "
                    "platform_chat_type=CASE WHEN ?<>'' THEN ? ELSE platform_chat_type END WHERE id=?",
                    (
                        item.get("platform_conv_id", ""),
                        item.get("platform_conv_id", ""),
                        item.get("chat_type", ""),
                        item.get("chat_type", ""),
                        conversation["id"],
                    ),
                )
            try:
                if existing:
                    incoming_id = existing["id"]
                else:
                    cursor = db.execute(
                        "INSERT INTO messages(conversation_id,role,content,platform_id,created_at,presentation) "
                        "VALUES(?,?,?,?,?,?)",
                        (
                            conversation["id"],
                            role,
                            item["content"],
                            item["platform_id"],
                            timestamp(item.get("timestamp")) or time(),
                            json.dumps(presentation, ensure_ascii=False),
                        ),
                    )
                    incoming_id = cursor.lastrowid
                if role == "customer":
                    db.execute("UPDATE conversations SET needs_human=1 WHERE id=?", (conversation["id"],))
                recalled = db.execute(
                    "SELECT presentation FROM messages WHERE conversation_id=? "
                    "AND json_extract(presentation,'$.withdraw_target')=? LIMIT 1",
                    (conversation["id"], item["platform_id"]),
                ).fetchone()
                if recalled:
                    notice = read_presentation(recalled["presentation"])
                    presentation = {
                        "version": 1,
                        "kind": "withdrawn",
                        "body": notice.get("body", "消息已撤回"),
                    }
                    db.execute(
                        "UPDATE messages SET presentation=? WHERE id=?",
                        (json.dumps(presentation), incoming_id),
                    )
            except sqlite3.IntegrityError:
                return
        name = valid_buyer_name(item.get("name"), item["uid"])
        if name:
            self.profiles.remember(shop_id, item["uid"], item.get("chat_type", ""), name)
        if role != "customer" or presentation.get("kind") == "withdrawn":
            return
        async with self.lock(conversation["id"]):
            conversation = self.store.one("SELECT * FROM conversations WHERE id=?", (conversation["id"],))
            shop = self.store.one("SELECT * FROM shops WHERE id=?", (shop_id,))
            if conversation["mode"] == "human" or shop["mode"] == "manual" or not item["automatic"]:
                self.store.execute("UPDATE conversations SET needs_human=1 WHERE id=?", (conversation["id"],))
                return
            incoming = self.store.one("SELECT created_at FROM messages WHERE id=?", (incoming_id,))
            history = self.store.all(
                "SELECT role,content FROM messages WHERE conversation_id=? "
                "AND (created_at<? OR (created_at=? AND id<?)) "
                "AND role IN ('customer','assistant','agent') "
                "AND COALESCE(json_extract(presentation,'$.kind'),'')<>'withdrawn' "
                "ORDER BY created_at DESC,id DESC LIMIT 10",
                (conversation["id"], incoming["created_at"], incoming["created_at"], incoming_id),
            )[::-1]
        # Generation must never hold the reply lock: staff can take over and reply immediately.
        try:
            result = await self.knowledge.answer(shop_id, item["content"], conversation["goods_id"], history)
        except ModelError as exc:
            self.store.execute(
                "INSERT INTO messages(conversation_id,role,content,state,created_at) "
                "VALUES(?,'system',?,'error',?)",
                (conversation["id"], "AI 未能生成回复，需要人工处理。" + str(exc), time()),
            )
            self.store.execute("UPDATE conversations SET needs_human=1 WHERE id=?", (conversation["id"],))
            return
        async with self.lock(conversation["id"]):
            current = self.store.one("SELECT * FROM conversations WHERE id=?", (conversation["id"],))
            if current["mode"] == "human":
                return
            latest = self.store.one(
                "SELECT id FROM messages WHERE conversation_id=? AND role='customer' "
                "ORDER BY created_at DESC,id DESC LIMIT 1",
                (conversation["id"],),
            )
            if latest["id"] != incoming_id:
                return
            incoming = self.store.one("SELECT presentation FROM messages WHERE id=?", (incoming_id,))
            if read_presentation(incoming["presentation"]).get("kind") == "withdrawn":
                return
            shop = self.store.one("SELECT * FROM shops WHERE id=?", (shop_id,))
            can_send = shop["mode"] == "auto" and not result["needs_human"] and bool(result["sources"])
            if can_send:
                await self.send(
                    conversation["id"],
                    result["answer"],
                    None,
                    f"ai:{incoming_id}",
                    role="assistant",
                    sources=result["sources"],
                )
            else:
                self.store.execute(
                    "INSERT INTO messages(conversation_id,role,content,state,sources,created_at) "
                    "VALUES(?,'draft',?,'draft',?,?)",
                    (
                        conversation["id"],
                        result["answer"],
                        json.dumps(result["sources"], ensure_ascii=False),
                        time(),
                    ),
                )
            self.store.execute(
                "UPDATE conversations SET needs_human=?,updated_at=? WHERE id=?",
                (int(not can_send), time(), conversation["id"]),
            )

    async def send(self, conversation_id, content, user_id, request_key, role="agent", sources=None):
        existing = self.store.one("SELECT * FROM messages WHERE request_key=?", (request_key,))
        if existing:
            if existing["conversation_id"] != conversation_id:
                raise ChannelError("发送标识重复，请刷新页面。")
            return existing
        conversation = self.store.one("SELECT * FROM conversations WHERE id=?", (conversation_id,))
        if not self.store.one(
            "SELECT id FROM shops WHERE id=? AND deleted_at IS NULL", (conversation["shop_id"],)
        ):
            raise ChannelError("店铺已删除，不能发送消息。")
        message_id = self.store.execute(
            "INSERT INTO messages(conversation_id,role,content,state,sources,"
            "request_key,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (
                conversation_id,
                role,
                content,
                "sending",
                json.dumps(sources or []),
                request_key,
                user_id,
                time(),
            ),
        )
        try:
            data = {
                "data": {
                    "cmd": "send_message",
                    "request_id": secrets.randbits(52),
                    "message": {
                        "to": {"role": "user", "uid": conversation["buyer_uid"]},
                        "from": {"role": "mall_cs"},
                        "content": content,
                        "msg_id": None,
                        "type": 0,
                        "is_aut": 0,
                        "manual_reply": 1,
                        "ts": int(time()),
                        "status": "read",
                        "is_read": 1,
                    },
                },
                "client": "WEB",
            }
            if conversation.get("platform_chat_type"):
                data["data"]["message"]["chat_type"] = conversation["platform_chat_type"]
            response = await self.api(
                self.cookies(conversation["shop_id"]), "/plateau/chat/send_message", data
            )
            if response.get("success") is not True or (response.get("result") or {}).get("error_code"):
                raise ChannelError("平台未确认消息发送成功，请在商家后台核对。")
        except (Exception, asyncio.CancelledError) as exc:
            self.store.execute("UPDATE messages SET state='uncertain' WHERE id=?", (message_id,))
            self.store.execute("UPDATE conversations SET needs_human=1 WHERE id=?", (conversation_id,))
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise ChannelError("发送结果未确认，请先在商家后台核对，避免重复发送。") from None
        self.store.execute("UPDATE messages SET state='sent' WHERE id=?", (message_id,))
        self.store.execute(
            "UPDATE conversations SET needs_human=0,updated_at=? WHERE id=?", (time(), conversation_id)
        )
        return self.store.one("SELECT * FROM messages WHERE id=?", (message_id,))

    async def focus_login(self, shop_id):
        page = self.login_pages.get(shop_id)
        if page is None or page.is_closed():
            raise ChannelError("没有正在等待登录的浏览器，请点击浏览器登录。")
        await page.bring_to_front()

    async def login(self, shop_id):
        task = self.login_tasks.get(shop_id)
        if task and not task.done():
            if shop_id in self.login_pages:
                await self.focus_login(shop_id)
            return
        self.login_states[shop_id] = {
            "status": "starting",
            "detail": "正在打开独立的 Chrome / Edge 登录窗口。",
        }
        self.login_tasks[shop_id] = asyncio.create_task(self._login(shop_id))

    async def cancel_login(self, shop_id):
        task = self.login_tasks.pop(shop_id, None)
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.login_states[shop_id] = {"status": "cancelled", "detail": "本次登录已取消，可以重新打开浏览器。"}

    async def _login(self, shop_id):
        from playwright.async_api import async_playwright

        context = None
        try:
            async with async_playwright() as playwright:
                profile = str(self.store.directory / "browser" / str(shop_id))
                for channel in ("chrome", "msedge", None):
                    try:
                        options = {"channel": channel} if channel else {}
                        context = await playwright.chromium.launch_persistent_context(
                            profile, headless=False, no_viewport=True, args=["--start-maximized"], **options
                        )
                        break
                    except Exception:
                        continue
                if context is None:
                    raise ChannelError(
                        "无法打开登录浏览器。请确认 Chrome / Edge 已安装，并关闭该店铺上次遗留的登录窗口。"
                    )
                page = context.pages[0] if context.pages else await context.new_page()
                self.login_pages[shop_id] = page
                await page.goto(
                    "https://mms.pinduoduo.com/chat-merchant/index.html", wait_until="domcontentloaded"
                )
                await page.bring_to_front()
                self.login_states[shop_id] = {
                    "status": "waiting",
                    "detail": "独立浏览器已打开，请在该窗口内完成账号或扫码登录及验证。",
                }
                deadline = monotonic() + 600
                identity_attempts = 0
                while monotonic() < deadline:
                    if page.is_closed():
                        self.login_states[shop_id] = {
                            "status": "cancelled",
                            "detail": "登录浏览器已关闭，可以重新打开。",
                        }
                        return
                    location = urlsplit(page.url)
                    if (
                        location.hostname not in {"mms.pinduoduo.com", "mms.yangkeduo.com"}
                        or location.path != "/chat-merchant/index.html"
                    ):
                        identity_attempts = 0
                        await asyncio.sleep(2)
                        continue
                    cookies = {
                        item["name"]: item["value"]
                        for item in await context.cookies()
                        if (
                            item.get("domain", "").lstrip(".") == "pinduoduo.com"
                            or item.get("domain", "").endswith(".pinduoduo.com")
                        )
                    }
                    if not cookies:
                        await asyncio.sleep(2)
                        continue
                    self.login_states[shop_id] = {
                        "status": "verifying",
                        "detail": "已检测到工作台，正在读取并核对店铺身份。",
                    }
                    try:
                        await self.bind(shop_id, cookies)
                    except ChannelError as exc:
                        identity_attempts += 1
                        self.login_states[shop_id] = {
                            "status": "verifying",
                            "detail": "店铺身份暂未确认：" + str(exc),
                        }
                        if identity_attempts >= 3:
                            raise ChannelError("浏览器已登录，但系统读取店铺身份失败：" + str(exc)) from None
                        await asyncio.sleep(2)
                        continue
                    self.login_states[shop_id] = {
                        "status": "done",
                        "detail": "店铺登录并绑定成功，点击连接消息开始接待。",
                    }
                    return
                raise ChannelError("等待登录超过 10 分钟，请重新打开浏览器。")
        except asyncio.CancelledError:
            self.login_states[shop_id] = {"status": "cancelled", "detail": "本次登录已取消。"}
            raise
        except Exception as exc:
            self.login_states[shop_id] = {
                "status": "failed",
                "detail": str(exc)
                if isinstance(exc, ChannelError)
                else "登录浏览器已关闭或发生异常，请重新打开后再试。",
            }
        finally:
            self.login_pages.pop(shop_id, None)
            if context:
                try:
                    await context.close()
                except Exception:
                    pass

    async def shutdown(self):
        await self.profiles.shutdown()
        tasks = list(self.tasks.values()) + list(self.login_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
