"""Fill reception transcripts from read-only history without activating reply processing."""

from __future__ import annotations

import asyncio
import json
from time import time

from .pdd_protocol import ProtocolError


class TranscriptSync:
    def __init__(self, store, history):
        self.store, self.history = store, history
        self.locks = {}

    def state(self, conversation_id):
        row = self.store.one("SELECT * FROM transcript_sync WHERE conversation_id=?", (conversation_id,))
        if not row:
            return {
                "status": "pending",
                "complete": False,
                "has_more": True,
                "pending_gap": False,
                "checked_at": 0,
                "error": "",
            }
        return {
            "status": "error" if row["error"] else "ok",
            "complete": not row["has_more"] and not row["gap_cursor"] and not row["error"],
            "has_more": bool(row["has_more"]),
            "pending_gap": bool(row["gap_cursor"]),
            "checked_at": row["checked_at"],
            "error": row["error"],
        }

    def save_messages(self, db, conversation_id, messages):
        for message in messages:
            db.execute(
                "INSERT INTO messages(conversation_id,role,content,state,platform_id,created_at,presentation) "
                "VALUES(?,?,?,'historical',?,?,?) ON CONFLICT(conversation_id,platform_id) DO UPDATE SET "
                "presentation=CASE WHEN json_extract(messages.presentation,'$.kind')='withdrawn' "
                "THEN messages.presentation ELSE excluded.presentation END,created_at=excluded.created_at",
                (
                    conversation_id,
                    message["role"],
                    message["content"],
                    message["platform_id"],
                    message["created_at"],
                    json.dumps(message["presentation"], ensure_ascii=False),
                ),
            )
        # A withdrawal may arrive on a different page, before or after its target message.
        recalls = db.execute(
            "SELECT platform_id,presentation FROM messages WHERE conversation_id=? "
            "AND json_extract(presentation,'$.withdraw_target') IS NOT NULL",
            (conversation_id,),
        ).fetchall()
        for recall in recalls:
            notice = json.loads(recall["presentation"])
            target = str(notice["withdraw_target"])
            if target == recall["platform_id"]:
                continue
            db.execute(
                "UPDATE messages SET presentation=? WHERE conversation_id=? AND platform_id=?",
                (
                    json.dumps({"version": 1, "kind": "withdrawn", "body": notice.get("body", "消息已撤回")}),
                    conversation_id,
                    target,
                ),
            )

    async def refresh(self, conversation, *, older=False, force=False):
        identifier = conversation["id"]
        async with self.locks.setdefault(identifier, asyncio.Lock()):
            shop = self.store.one(
                "SELECT cookies,deleted_at FROM shops WHERE id=?", (conversation["shop_id"],)
            )
            if not shop or shop["deleted_at"] or not shop["cookies"]:
                return {
                    "status": "unavailable",
                    "complete": False,
                    "has_more": False,
                    "pending_gap": False,
                    "checked_at": 0,
                    "error": "店铺未绑定有效登录态，当前仅显示本地记录。",
                }
            current = self.store.one("SELECT * FROM transcript_sync WHERE conversation_id=?", (identifier,))
            if current and not older and not force and time() - current["checked_at"] < 5:
                return self.state(identifier)
            if current and older and not current["has_more"] and not current["gap_cursor"]:
                return self.state(identifier)
            current = current or {
                "cursor": "",
                "has_more": 1,
                "head_id": "",
                "gap_cursor": "",
                "gap_target": "",
            }
            before = current["gap_cursor"] or (current["cursor"] if older else "")
            filling_gap = bool(current["gap_cursor"])
            try:
                async with asyncio.timeout(8):
                    page = await self.history.messages(
                        conversation["shop_id"],
                        conversation["buyer_uid"],
                        before,
                        size=50,
                        chat_type=conversation["platform_chat_type"],
                    )
                messages = page["messages"]
                ids = {message["platform_id"] for message in messages}
                oldest = messages[0]["platform_id"] if messages else ""
                newest = messages[-1]["platform_id"] if messages else ""
                cursor, more = current["cursor"], current["has_more"]
                head, gap, target = current["head_id"], current["gap_cursor"], current["gap_target"]
                if filling_gap:
                    if target in ids or not page["has_more"]:
                        gap, target = "", ""
                    else:
                        gap = oldest
                    if not page["has_more"]:
                        cursor, more = oldest, False
                elif before or not head:
                    cursor, more = oldest, page["has_more"]
                    head = head or newest
                else:
                    if head not in ids and page["has_more"]:
                        gap, target = oldest, head
                    if cursor in ids or not page["has_more"]:
                        cursor, more = oldest, page["has_more"]
                    head = newest or head
                with self.store.connect() as db:
                    active = db.execute(
                        "SELECT s.deleted_at FROM shops s JOIN conversations c ON c.shop_id=s.id WHERE c.id=?",
                        (identifier,),
                    ).fetchone()
                    if not active or active["deleted_at"]:
                        return {
                            "status": "unavailable",
                            "complete": False,
                            "has_more": False,
                            "error": "店铺已删除。",
                        }
                    self.save_messages(db, identifier, messages)
                    db.execute(
                        "INSERT INTO transcript_sync(conversation_id,checked_at,cursor,has_more,head_id,gap_cursor,gap_target,error) "
                        "VALUES(?,?,?,?,?,?,?,'') ON CONFLICT(conversation_id) DO UPDATE SET "
                        "checked_at=excluded.checked_at,cursor=excluded.cursor,has_more=excluded.has_more,head_id=excluded.head_id,"
                        "gap_cursor=excluded.gap_cursor,gap_target=excluded.gap_target,error=''",
                        (identifier, time(), cursor, int(more), head, gap, target),
                    )
            except (ProtocolError, TimeoutError) as exc:
                error = (
                    str(exc)
                    if isinstance(exc, ProtocolError)
                    else "平台记录读取超时，可重试；本地记录仍可浏览。"
                )
                self.store.execute(
                    "INSERT INTO transcript_sync(conversation_id,checked_at,error) VALUES(?,?,?) "
                    "ON CONFLICT(conversation_id) DO UPDATE SET checked_at=excluded.checked_at,error=excluded.error",
                    (identifier, time(), error),
                )
            return self.state(identifier)
