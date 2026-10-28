"""Small, shop-scoped nickname cache. No other buyer profile fields are retained."""

from __future__ import annotations

import asyncio
from time import time

from .pdd_protocol import ProtocolError, valid_buyer_name


class BuyerProfiles:
    def __init__(self, store, channels):
        self.store, self.channels = store, channels
        self.semaphore = asyncio.Semaphore(4)
        self.pending = {}

    def cached(self, shop_id, uid, chat_type=""):
        return self.store.one(
            "SELECT nickname,next_check FROM buyer_profiles WHERE shop_id=? AND buyer_uid=? AND chat_type=?",
            (shop_id, uid, chat_type),
        )

    def remember(self, shop_id, uid, chat_type, name, ttl=86400):
        self.store.execute(
            "INSERT INTO buyer_profiles(shop_id,buyer_uid,chat_type,nickname,next_check) VALUES(?,?,?,?,?) "
            "ON CONFLICT(shop_id,buyer_uid,chat_type) DO UPDATE SET "
            "nickname=excluded.nickname,next_check=excluded.next_check",
            (shop_id, uid, chat_type, name, time() + ttl),
        )
        if name:
            # A name correction must not reorder conversations or affect their reception mode.
            self.store.execute(
                "UPDATE conversations SET buyer_name=? WHERE shop_id=? AND buyer_uid=? AND buyer_name<>?",
                (name, shop_id, uid, name),
            )

    async def resolve(self, shop_id, uid, chat_type="", hint="", fallback=""):
        uid, chat_type = str(uid), str(chat_type)
        hint = valid_buyer_name(hint, uid)
        if hint:
            self.remember(shop_id, uid, chat_type, hint)
            return hint
        cached = self.cached(shop_id, uid, chat_type)
        fallback = valid_buyer_name((cached or {}).get("nickname"), uid) or valid_buyer_name(fallback, uid)
        if cached and cached["next_check"] > time():
            return fallback or uid
        key = (shop_id, uid, chat_type)
        if key not in self.pending:
            self.pending[key] = asyncio.create_task(self.fetch(shop_id, uid, chat_type, fallback))
            self.pending[key].add_done_callback(lambda task: self.pending.pop(key, None))
        return await asyncio.shield(self.pending[key])

    async def fetch(self, shop_id, uid, chat_type, fallback):
        async with self.semaphore:
            try:
                body = {"uid": uid}
                if chat_type:
                    body["chatType"] = chat_type
                async with asyncio.timeout(3):
                    response = await self.channels.api(
                        self.channels.cookies(shop_id), "/latitude/user/userInfoWithTags", body
                    )
                profile = response.get("result")
                profile = profile if isinstance(profile, dict) else {}
                identity = profile.get("uid")
                name = (
                    valid_buyer_name(profile.get("nickname"), uid)
                    if identity is not None and str(identity) == uid
                    else ""
                )
            except (ProtocolError, TimeoutError):
                name = ""
            # A failed lookup is retried later; never overwrite a known name with a UID.
            ttl = 86400 if name else 300
            name = name or fallback
            self.remember(shop_id, uid, chat_type, name, ttl)
            return name or uid

    async def enrich(self, shop_id, items, embedded=False):
        # Advance through uncached contacts on later polls instead of querying the first 20 forever.
        candidates = []
        for item in items:
            uid = item["buyer_uid"]
            chat_type = item.get("chat_type", item.get("platform_chat_type", ""))
            hint = valid_buyer_name(item.get("buyer_name"), uid) if embedded else ""
            if hint:
                self.remember(shop_id, uid, chat_type, hint)
                continue
            cached = self.cached(shop_id, uid, chat_type)
            if cached and cached["nickname"]:
                item["buyer_name"] = cached["nickname"]
            if not cached or cached["next_check"] <= time():
                candidates.append(item)

        async def update(item):
            item["buyer_name"] = await self.resolve(
                shop_id,
                item["buyer_uid"],
                item.get("chat_type", item.get("platform_chat_type", "")),
                fallback=item.get("buyer_name", ""),
            )

        await asyncio.gather(*(update(item) for item in candidates[:20]))
        return items

    async def shutdown(self):
        tasks = list(self.pending.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
