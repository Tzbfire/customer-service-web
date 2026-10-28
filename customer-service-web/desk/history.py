"""Read-only platform history browsing, with explicit import for a human takeover."""

from __future__ import annotations

import asyncio
import json
import secrets
from time import time

from .message_content import normalize_message, read_presentation
from .pdd_protocol import ProtocolError, buyer_nickname


class HistoryConflict(ProtocolError):
    pass


def format_message(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get("message", raw), dict):
        raise ProtocolError("历史消息条目格式不正确。")
    return normalize_message(raw.get("message", raw))


class HistoryService:
    def __init__(self, store, channels):
        self.store, self.channels = store, channels
        self.backfill_checks = {}

    async def conversations(self, shop_id, page=1, size=20):
        response = await self.channels.api(
            self.channels.cookies(shop_id),
            "/plateau/chat/latest_conversations",
            {
                "data": {
                    "cmd": "latest_conversations",
                    "request_id": secrets.randbits(52),
                    "version": 2,
                    "need_unreply_time": True,
                    "page": page,
                    "size": size,
                },
                "client": "WEB",
            },
        )
        result = response.get("result")
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("conversations"), list)
            or "has_more" not in result
        ):
            raise ProtocolError("平台历史会话列表格式不正确。")
        items = []
        for entry in result["conversations"]:
            message = entry.get("message", entry) if isinstance(entry, dict) else None
            if not isinstance(message, dict):
                raise ProtocolError("平台历史会话条目格式不正确。")
            sender = message.get("from") or {}
            recipient = message.get("to") or {}
            buyer = (
                sender
                if isinstance(sender, dict) and sender.get("role") == "user"
                else recipient
                if isinstance(recipient, dict) and recipient.get("role") == "user"
                else {}
            )
            uid = buyer.get("uid") or buyer.get("id")
            if uid is None:
                continue
            value = format_message(message)
            local = self.store.one(
                "SELECT id FROM conversations WHERE shop_id=? AND buyer_uid=?", (shop_id, str(uid))
            )
            items.append(
                {
                    "buyer_uid": str(uid),
                    "buyer_name": buyer_nickname(message, uid) or str(uid),
                    "preview": value["content"],
                    "last_message_id": value["platform_id"],
                    "updated_at": value["created_at"],
                    "goods_id": value["goods_id"],
                    "chat_type": value["chat_type"],
                    "local_conversation_id": local["id"] if local else None,
                }
            )
        await self.channels.profiles.enrich(shop_id, items, embedded=True)
        total = result.get("total_count", result.get("total"))
        total = total if isinstance(total, int) and not isinstance(total, bool) and total >= 0 else None
        return {
            "items": items,
            "page": page,
            "size": size,
            "has_more": bool(result["has_more"]),
            "total": total,
            "returned_count": len(items),
        }

    async def messages(self, shop_id, buyer_uid, before="", size=50, chat_type=""):
        options = {"with": {"role": "user", "id": buyer_uid}, "start_index": 0, "size": size}
        if before:
            options["start_msg_id"] = before
        if chat_type:
            options["chat_type"] = chat_type
        response = await self.channels.api(
            self.channels.cookies(shop_id),
            "/plateau/chat/list",
            {
                "data": {
                    "cmd": "list",
                    "request_id": secrets.randbits(52),
                    "list": options,
                    "notUpdateUnreplyTs": True,
                },
                "client": "WEB",
            },
        )
        result = response.get("result")
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("messages"), list)
            or "has_more" not in result
        ):
            raise ProtocolError("平台历史消息列表格式不正确。")
        normalized = [format_message(message) for message in result["messages"]]
        unique = {
            message["platform_id"]: message for message in normalized if message["platform_id"] is not None
        }
        messages = sorted(
            unique.values(), key=lambda m: (m["created_at"], len(m["platform_id"]), m["platform_id"])
        )
        by_id = {m["platform_id"]: m for m in messages}
        for message in messages:
            target = by_id.get(message["presentation"].get("withdraw_target"))
            if target and target is not message:
                target["presentation"] = {
                    "version": 1,
                    "kind": "withdrawn",
                    "body": message["presentation"]["body"],
                }
                message["presentation"]["hidden"] = True
        has_more = bool(result["has_more"])
        cursor = messages[0]["platform_id"] if messages else None
        if has_more and (not cursor or cursor == before):
            raise ProtocolError("历史消息分页没有推进，请刷新会话后重试。")
        return {
            "messages": messages,
            "has_more": has_more,
            "next_cursor": cursor if has_more else None,
            "returned_count": len(messages),
            "total": len(messages) if not before and not has_more else None,
        }

    async def open_for_reply(self, shop_id, buyer_uid, buyer_name, user, chat_type=""):
        # This entry point is only invoked by the operator's explicit "take over" action.
        buyer_name = await self.channels.profiles.resolve(shop_id, buyer_uid, chat_type, fallback=buyer_name)
        history = await self.messages(shop_id, buyer_uid, chat_type=chat_type)
        with self.store.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO conversations(shop_id,buyer_uid,buyer_name,updated_at)VALUES(?,?,?,?)",
                (shop_id, buyer_uid, buyer_name, time()),
            )
            conversation = db.execute(
                "SELECT * FROM conversations WHERE shop_id=? AND buyer_uid=?", (shop_id, buyer_uid)
            ).fetchone()
            if conversation["assignee"] not in (None, user["id"]) and user["role"] != "admin":
                raise HistoryConflict("会话已经由其他客服接管。")
            identifier = conversation["id"]
            db.execute(
                "UPDATE conversations SET mode='human',assignee=?,needs_human=0 WHERE id=?",
                (user["id"], identifier),
            )
            for message in history["messages"]:
                content = message["content"]
                db.execute(
                    "INSERT INTO messages(conversation_id,role,content,state,platform_id,created_at,presentation)"
                    "VALUES(?,?,?,'historical',?,?,?) ON CONFLICT(conversation_id,platform_id) "
                    "DO UPDATE SET presentation=excluded.presentation",
                    (
                        identifier,
                        message["role"],
                        content,
                        message["platform_id"],
                        message["created_at"],
                        json.dumps(message["presentation"], ensure_ascii=False),
                    ),
                )
            newest = next((m for m in reversed(history["messages"]) if m["goods_id"]), None)
            if newest and not conversation["goods_id"]:
                db.execute("UPDATE conversations SET goods_id=? WHERE id=?", (newest["goods_id"], identifier))
        return {"conversation_id": identifier, "imported_page_count": len(history["messages"])}

    async def enrich_local_messages(self, conversation, messages):
        """Restore presentation for existing messages only; never import or reply."""
        missing = {
            m["platform_id"]
            for m in messages
            if m.get("platform_id")
            and not read_presentation(m.get("presentation"))
            and ("【" in m["content"] or m["content"].startswith("https://"))
        }
        key = (conversation["id"], tuple(sorted(missing)))
        if missing and self.backfill_checks.get(key, 0) <= time():
            self.backfill_checks[key] = time() + 300
            before = ""
            try:
                async with asyncio.timeout(8):
                    for _ in range(3):
                        page = await self.messages(
                            conversation["shop_id"],
                            conversation["buyer_uid"],
                            before,
                            chat_type=conversation["platform_chat_type"],
                        )
                        for item in page["messages"]:
                            if item["platform_id"] in missing:
                                self.store.execute(
                                    "UPDATE messages SET presentation=? WHERE conversation_id=? AND platform_id=?",
                                    (
                                        json.dumps(item["presentation"], ensure_ascii=False),
                                        conversation["id"],
                                        item["platform_id"],
                                    ),
                                )
                                for message in messages:
                                    if message["platform_id"] == item["platform_id"]:
                                        message["presentation"] = item["presentation"]
                                missing.discard(item["platform_id"])
                        if not missing or not page["has_more"]:
                            break
                        before = page["next_cursor"]
            except (ProtocolError, TimeoutError):
                pass  # The saved transcript remains usable while the platform is unavailable.
        for message in messages:
            message["presentation"] = read_presentation(message.get("presentation"))
        return messages
