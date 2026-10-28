"""Merchant catalog snapshots and product-scoped knowledge from live-verified endpoints."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from time import time
from uuid import uuid4

import httpx

from .knowledge import file_hash, image_tiles
from .llm import ModelError
from .message_content import media_url, money, safe_url, text
from .pdd_protocol import ProtocolError


LIST_PATH = "/latitude/goods/recommendGoods"
DETAIL_PATH = "/glide/v2/mms/query/commit/on_shop/detail"


def stamp(value):
    return datetime.fromtimestamp(value, timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


def normalize_product(item, detail=None):
    detail = detail or {}
    goods_id = str(item.get("goodsId") or detail.get("goods_id") or "")
    name = text(detail.get("goods_name") or item.get("goodsName"), 500)
    if not goods_id or not name:
        raise ProtocolError("商品接口缺少商品 ID 或名称。")
    images = []
    for image in detail.get("detail_gallery") or []:
        if isinstance(image, dict) and not image.get("is_deleted") and media_url(image.get("url")):
            if image["url"] not in images:
                images.append(image["url"])
    skus = []
    for sku in detail.get("skus") or []:
        if not isinstance(sku, dict):
            continue
        specs = sku.get("spec") or []
        spec = " | ".join(
            (text(x.get("parent_name"), 100) + "：" if x.get("parent_name") else "")
            + text(x.get("spec_name"), 300)
            for x in specs
            if isinstance(x, dict) and x.get("spec_name")
        )
        skus.append(
            {
                "id": str(sku.get("sku_id") or sku.get("id") or ""),
                "spec": spec,
                "price": money(sku.get("multi_price"), cents=True),
                "on_sale": sku.get("is_onsale") == 1,
            }
        )
    properties = []
    for prop in detail.get("goods_property_list") or []:
        if isinstance(prop, dict):
            label = text(prop.get("prop_name") or prop.get("name"), 200)
            value = prop.get("value") or prop.get("value_name") or prop.get("values")
            if isinstance(value, list):
                value = "、".join(text(v) for v in value if isinstance(v, (str, int, float)))
            if label and text(value):
                properties.append(label + "：" + text(value, 2000))
    return {
        "goods_id": goods_id,
        "name": name,
        "thumb_url": media_url(detail.get("thumb_url") or item.get("thumbUrl")) or "",
        "goods_url": safe_url(item.get("goodsUrl"))
        or ("https://mobile.yangkeduo.com/goods.html?goods_id=" + goods_id if goods_id.isdigit() else ""),
        "description": text(detail.get("goods_desc"), 20000),
        "warm_tips": text(detail.get("warm_tips"), 5000),
        "categories": [text(c, 300) for c in detail.get("cats") or [] if isinstance(c, str) and c],
        "properties": properties,
        "skus": skus,
        "detail_images": images,
        "price_min": money(item.get("minOnSaleGroupPrice"), cents=True),
        "price_max": money(item.get("maxOnSaleGroupPrice"), cents=True),
    }


def knowledge_text(product):
    lines = [
        "来源：本店拼多多商品资料（平台同步）",
        "商品名称：" + product["name"],
        "商品 ID：" + product["goods_id"],
        "商品链接：" + product["goods_url"],
        "以下价格为同步时参考价，不代表实时促销、库存或成交承诺；以商品页面为准。",
    ]
    if product["price_min"]:
        lines.append(
            "同步参考价格区间："
            + product["price_min"]
            + (
                " — " + product["price_max"]
                if product["price_max"] and product["price_max"] != product["price_min"]
                else ""
            )
        )
    if product["categories"]:
        lines.append("商品分类：" + " > ".join(product["categories"]))
    if product["description"]:
        lines.extend(["商品说明：", product["description"]])
    if product["warm_tips"]:
        lines.extend(["注意事项：", product["warm_tips"]])
    lines.extend(product["properties"])
    if product["skus"]:
        lines.append("SKU 规格列表（只代表平台已提供的组合，不推断其他属性）：")
        for sku in product["skus"]:
            if sku["on_sale"]:
                lines.append(
                    f"{product['name']} · 商品 {product['goods_id']} · SKU {sku['id']}：{sku['spec']}"
                    + ("；参考拼单价 " + sku["price"] if sku["price"] else "")
                )
    lines.append(
        f"商品详情图 {len(product['detail_images'])} 张。图片尚未识别的文字不属于已知知识，请使用详情图识别并审核入库。"
    )
    return "\n".join(lines)


class Products:
    def __init__(self, store, channels, knowledge):
        self.store, self.channels, self.knowledge = store, channels, knowledge
        self.tasks = {}
        store.execute(
            "UPDATE product_sync SET status='interrupted',error=? WHERE status='running'",
            ("服务重启中断同步，可重新同步。",),
        )
        store.execute(
            "UPDATE products SET image_state='failed',image_error=? WHERE image_state='running'",
            ("服务重启中断识别，可重试；已完成图片会复用缓存。",),
        )

    def active(self, shop_id):
        return any(key[0] == shop_id and not task.done() for key, task in self.tasks.items())

    def start(self, shop_id, user_id):
        self.channels.cookies(shop_id)
        if self.active(shop_id):
            raise ProtocolError("此店铺已有商品任务运行，请等待完成。")
        run_id = uuid4().hex
        self.store.execute(
            "INSERT INTO product_sync(shop_id,run_id,status,updated_at) VALUES(?,?,'running',?) "
            "ON CONFLICT(shop_id) DO UPDATE SET run_id=excluded.run_id,status='running',total=0,completed=0,failed=0,error='',updated_at=excluded.updated_at",
            (shop_id, run_id, time()),
        )
        self.launch((shop_id, "sync"), self.sync(shop_id, user_id, run_id))

    def launch(self, key, coro):
        task = asyncio.create_task(coro)
        self.tasks[key] = task
        task.add_done_callback(lambda done: self.tasks.pop(key, None))

    def update_status(self, shop_id, **values):
        self.store.execute(
            "UPDATE product_sync SET " + ",".join(k + "=?" for k in values) + ",updated_at=? WHERE shop_id=?",
            (*values.values(), time(), shop_id),
        )

    def write_document(self, shop_id, goods_id, title, content, user_id, document_id=None, publish=False):
        document_id = document_id or uuid4().hex
        filename = f"pdd-goods-{goods_id}.md"
        existing = self.store.one("SELECT id FROM documents WHERE id=?", (document_id,))
        if existing:
            self.store.execute(
                "UPDATE documents SET title=?,content=?,state='draft',error='' WHERE id=?",
                (title, content, document_id),
            )
        else:
            self.store.execute(
                "INSERT INTO documents(id,shop_id,title,goods_id,category,filename,sha256,content,state,created_by,created_at) VALUES(?,?,?,?,'product',?,?,?,'draft',?,?)",
                (
                    document_id,
                    shop_id,
                    title,
                    goods_id,
                    filename,
                    file_hash(("platform:" + document_id).encode()),
                    content,
                    user_id,
                    time(),
                ),
            )
        (self.store.directory / "uploads" / document_id).write_text(content, encoding="utf-8")
        if publish:
            self.knowledge.publish(document_id, title, goods_id, "product", content)
        return document_id

    async def sync(self, shop_id, user_id, run_id):
        completed = failed = 0
        try:
            shop = self.store.one("SELECT platform_shop_id FROM shops WHERE id=?", (shop_id,))
            page = 1
            seen = set()
            catalog = {}
            while True:
                data = await self.channels.api(
                    self.channels.cookies(shop_id), LIST_PATH, {"uid": "", "pageNum": page, "pageSize": 20}
                )
                result = data.get("result")
                if not isinstance(result, dict) or not isinstance(result.get("onSaleGoods"), list):
                    raise ProtocolError("平台商品列表格式不正确。")
                items = result["onSaleGoods"]
                signature = tuple(str(item.get("goodsId")) for item in items if isinstance(item, dict))
                if items and signature in seen:
                    raise ProtocolError("商品列表分页重复，已停止同步。")
                seen.add(signature)
                for item in items:
                    product = normalize_product(item)
                    catalog[product["goods_id"]] = item
                total = result.get("total")
                self.update_status(shop_id, total=len(catalog))
                if isinstance(total, int) and not isinstance(total, bool):
                    if len(catalog) >= total:
                        break
                    if not items:
                        raise ProtocolError("平台商品列表提前结束，尚未拉取完整。")
                elif len(items) < 20:
                    break
                page += 1
            for goods_id, item in catalog.items():
                try:
                    self.store.execute(
                        "INSERT INTO products(shop_id,goods_id,name,seen_run,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(shop_id,goods_id) DO UPDATE SET seen_run=excluded.seen_run",
                        (shop_id, goods_id, text(item.get("goodsName"), 500), run_id, time()),
                    )
                    response = await self.channels.api(
                        self.channels.cookies(shop_id), DETAIL_PATH, {"goods_id": goods_id}
                    )
                    detail = response.get("result")
                    if not isinstance(detail, dict) or str(detail.get("goods_id")) != goods_id:
                        raise ProtocolError("商品详情与请求 ID 不匹配。")
                    if str(detail.get("mall_id")) != str(shop["platform_shop_id"]):
                        raise ProtocolError("商品详情不属于当前店铺，已拒绝入库。")
                    product = normalize_product(item, detail)
                    content = knowledge_text(product)
                    old = self.store.one(
                        "SELECT document_id,generated_hash FROM products WHERE shop_id=? AND goods_id=?",
                        (shop_id, goods_id),
                    )
                    doc = (
                        self.store.one("SELECT * FROM documents WHERE id=?", (old["document_id"],))
                        if old["document_id"]
                        else None
                    )
                    edited = (
                        doc
                        and old["generated_hash"]
                        and file_hash(doc["content"].encode()) != old["generated_hash"]
                    )
                    document_id = old["document_id"]
                    note = "人工编辑过商品知识，已保留编辑内容；平台快照已更新。" if edited else ""
                    generated_hash = old["generated_hash"]
                    if not edited:
                        document_id = self.write_document(
                            shop_id,
                            goods_id,
                            "平台商品 · " + product["name"],
                            content,
                            user_id,
                            document_id,
                            publish=True,
                        )
                        generated_hash = file_hash(content.encode())
                    self.store.execute(
                        "UPDATE products SET name=?,thumb_url=?,goods_url=?,payload=?,state='ready',error=?,document_id=?,generated_hash=?,available=1,updated_at=? WHERE shop_id=? AND goods_id=?",
                        (
                            product["name"],
                            product["thumb_url"],
                            product["goods_url"],
                            json.dumps(product, ensure_ascii=False),
                            note,
                            document_id,
                            generated_hash,
                            time(),
                            shop_id,
                            goods_id,
                        ),
                    )
                    completed += 1
                except (ProtocolError, ValueError, OSError) as exc:
                    failed += 1
                    self.store.execute(
                        "UPDATE products SET state='failed',error=? WHERE shop_id=? AND goods_id=?",
                        (
                            str(exc) if isinstance(exc, ProtocolError) else "商品资料保存失败，请重试。",
                            shop_id,
                            goods_id,
                        ),
                    )
                self.update_status(shop_id, completed=completed, failed=failed)
                await asyncio.sleep(0)
            # Only a completed enumeration can mark products missing from this saleable catalog.
            with self.store.connect() as db:
                db.execute(
                    "UPDATE products SET available=0 WHERE shop_id=? AND seen_run<>?", (shop_id, run_id)
                )
                db.execute(
                    "UPDATE documents SET state='draft' WHERE id IN (SELECT document_id FROM products WHERE shop_id=? AND available=0)",
                    (shop_id,),
                )
                db.execute(
                    "UPDATE documents SET state='draft' WHERE id IN (SELECT image_document_id FROM products WHERE shop_id=? AND available=0)",
                    (shop_id,),
                )
            self.update_status(shop_id, status="partial" if failed else "done")
        except asyncio.CancelledError:
            self.update_status(shop_id, status="interrupted", error="同步已中断，可重新同步。")
            raise
        except Exception as exc:
            self.update_status(
                shop_id,
                status="failed",
                error=str(exc) if isinstance(exc, ProtocolError) else "商品同步失败，请稍后重试。",
            )

    def start_images(self, shop_id, goods_id, user_id):
        if self.active(shop_id):
            raise ProtocolError("此店铺已有商品任务运行，请等待完成。")
        product = self.store.one("SELECT * FROM products WHERE shop_id=? AND goods_id=?", (shop_id, goods_id))
        if not product or not product["available"]:
            raise ProtocolError("请先同步当前在售商品。")
        if not self.store.model().get("vision_enabled"):
            raise ModelError("请先在模型配置中启用图片识别。")
        if not json.loads(product["payload"]).get("detail_images"):
            raise ProtocolError("此商品没有可识别的详情图片。")
        self.store.execute(
            "UPDATE products SET image_state='running',image_error='' WHERE shop_id=? AND goods_id=?",
            (shop_id, goods_id),
        )
        self.launch((shop_id, goods_id), self.ocr(product, user_id))

    async def fetch_image(self, url):
        if not media_url(url):
            raise ProtocolError("商品详情图地址不属于支持的平台媒体域名。")
        async with httpx.AsyncClient(
            timeout=25, follow_redirects=False, trust_env=False, transport=self.channels.transport
        ) as client:
            async with client.stream("GET", url) as response:
                if response.status_code != 200 or not response.headers.get("content-type", "").startswith(
                    "image/"
                ):
                    raise ProtocolError("商品详情图下载失败。")
                chunks = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > 12 * 1024 * 1024:
                        raise ProtocolError("单张商品详情图超过 12 MB。")
                    chunks.append(chunk)
                return b"".join(chunks)

    async def ocr(self, record, user_id):
        shop_id, goods_id = record["shop_id"], record["goods_id"]
        try:
            product = json.loads(record["payload"])
            images = product["detail_images"]
            if len(images) > 30:
                raise ProtocolError("商品详情图超过 30 张，请分批上传图片资料。")
            parts = [
                "商品名称：" + product["name"],
                "商品 ID：" + goods_id,
                "来源：本店商品详情图识别结果，需人工核对数字、单位与适用条件。",
            ]
            for index, url in enumerate(images, 1):
                cached = self.store.one(
                    "SELECT content FROM product_image_text WHERE shop_id=? AND goods_id=? AND url=?",
                    (shop_id, goods_id, url),
                )
                try:
                    if cached:
                        content = cached["content"]
                    else:
                        data = await self.fetch_image(url)
                        content = "\n".join(
                            [
                                await self.knowledge.model.transcribe(tile, mime="image/jpeg")
                                for tile in await asyncio.to_thread(image_tiles, data)
                            ]
                        )
                        self.store.execute(
                            "INSERT INTO product_image_text(shop_id,goods_id,url,content,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(shop_id,goods_id,url) DO UPDATE SET content=excluded.content,updated_at=excluded.updated_at",
                            (shop_id, goods_id, url, content, time()),
                        )
                except (ModelError, ProtocolError, ValueError) as exc:
                    raise ModelError(f"第 {index}/{len(images)} 张详情图识别失败：{exc}") from None
                parts.extend([f"详情图 {index}：{url}", content])
            content = "\n\n".join(parts)
            previous = (
                self.store.one("SELECT * FROM documents WHERE id=?", (record["image_document_id"],))
                if record["image_document_id"]
                else None
            )
            if previous and previous["content"] == content:
                document_id = previous["id"]
            else:
                document_id = self.write_document(
                    shop_id, goods_id, "详情图识别 · " + product["name"], content, user_id, publish=False
                )
                if previous:
                    self.store.execute("UPDATE documents SET state='draft' WHERE id=?", (previous["id"],))
            self.store.execute(
                "UPDATE products SET image_document_id=?,image_state='draft',image_error='' WHERE shop_id=? AND goods_id=?",
                (document_id, shop_id, goods_id),
            )
        except asyncio.CancelledError:
            self.store.execute(
                "UPDATE products SET image_state='failed',image_error='识别中断，可重试并复用已完成图片。' WHERE shop_id=? AND goods_id=?",
                (shop_id, goods_id),
            )
            raise
        except Exception as exc:
            self.store.execute(
                "UPDATE products SET image_state='failed',image_error=? WHERE shop_id=? AND goods_id=?",
                (
                    str(exc)
                    if isinstance(exc, (ProtocolError, ModelError))
                    else "详情图识别失败，请稍后重试。",
                    shop_id,
                    goods_id,
                ),
            )

    async def shutdown(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
