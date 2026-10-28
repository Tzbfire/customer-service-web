"""Normalize platform content once for history, live reception and stored transcripts."""

from __future__ import annotations

import json
import math
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

MEDIA_DOMAINS = ("pddpic.com", "yangkeduo.com", "pinduoduo.com")
SYSTEM_TYPES = {20, 24, 30, 31, 41, 56, 97, 1002}


def text(value, limit=20000):
    return (
        value[:limit]
        if isinstance(value, str)
        else str(value)[:limit]
        if isinstance(value, (int, float))
        else ""
    )


def timestamp(value):
    try:
        result = float(value)
        if not math.isfinite(result) or result < 0:
            return 0
        return result / 1000 if result > 100_000_000_000 else result
    except (ValueError, TypeError, OverflowError):
        return 0


def safe_url(value):
    if not isinstance(value, str) or len(value) > 5000 or any(ord(c) < 32 for c in value):
        return None
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and not parsed.username
            and not parsed.password
            and parsed.port in (None, 80, 443)
        ):
            return value
    except ValueError:
        pass
    return None


def media_url(value):
    value = safe_url(value)
    if not value:
        return None
    parsed = urlsplit(value)
    if parsed.scheme != "https" or parsed.port not in (None, 443):
        return None
    host = parsed.hostname.lower()
    return value if any(host == domain or host.endswith("." + domain) for domain in MEDIA_DOMAINS) else None


def first(sources, *keys):
    for source in sources:
        for key in keys:
            value = source.get(key)
            if value is not None and value != "":
                return value
    return None


def money(value, cents=False):
    if isinstance(value, bool) or value is None:
        return ""
    try:
        number = Decimal(str(value)) / (100 if cents else 1)
        if not number.is_finite() or number < 0 or number > 100000000:
            return ""
        return "¥" + format(number.quantize(Decimal("0.01")), "f")
    except (InvalidOperation, ValueError):
        return ""


def normalize_message(message):
    sender = message.get("from")
    sender = sender if isinstance(sender, dict) else {}
    role = sender.get("role")
    try:
        kind = int(message.get("type"))
    except (ValueError, TypeError):
        kind = None
    try:
        subtype = int(message.get("sub_type"))
    except (ValueError, TypeError):
        subtype = None
    info = message.get("info")
    info = info if isinstance(info, dict) else {}
    data = info.get("data")
    data = data if isinstance(data, dict) else {}
    sources = [info, data]
    sources += [v for v in (info.get("goods_info"), data.get("goods_info")) if isinstance(v, dict)]
    content = message.get("content") or ""
    if isinstance(content, dict):
        content_object = content
        content = text(content.get("text") or content.get("url"))
    else:
        content_object = {}
        content = text(content)
    goods_id = text(first(sources, "goodsID", "goods_id", "goodsId"), 100)
    product = None
    if goods_id:
        url = safe_url(first(sources, "linkUrl", "link_url", "mall_link_url"))
        if not url and goods_id.isdigit():
            url = "https://mobile.yangkeduo.com/goods.html?goods_id=" + goods_id
        price = first(sources, "goodsPrice")  # This field is in yuan in the live goods inquiry card.
        product = {
            "id": goods_id,
            "name": text(first(sources, "goodsName", "goods_name"), 500) or "商品咨询",
            "image_url": media_url(first(sources, "goodsThumbUrl", "goods_thumb_url", "thumb_url")),
            "url": url,
            "price": money(price),
            "coupon_price": money(first(sources, "couponPromoPrice")),
            "spec": text(first(sources, "spec", "specStr", "spec_str"), 1000),
        }
    order_id = text(first(sources, "orderSequenceNo", "order_sequence_no", "orderSn", "order_sn"), 100)
    order = None
    if kind == 8 or (kind == 0 and subtype == 1) or order_id:
        order = {
            "id": order_id,
            "spec": text(first(sources, "spec", "specStr"), 1000),
            "after_sales_status": text(first(sources, "afterSalesStatus", "after_sales_status"), 200),
            "after_sales_type": text(first(sources, "afterSalesType", "after_sales_type"), 200),
            "status": text(first(sources, "orderStatusText", "order_status_text", "statusDesc"), 200),
        }
    attachments = []
    presentation = {"version": 1, "kind": "text", "body": content, "product": product, "order": order}
    if kind in {1, 14, 5}:
        source = first([info, content_object], "image_url", "video_url", "url") or content
        url = safe_url(source)
        media_kind = "video" if kind == 14 else "image" if kind == 1 else "emotion"
        if url:
            attachments.append(
                {
                    "label": "查看视频" if kind == 14 else "查看图片",
                    "url": url,
                    "kind": media_kind,
                    "inline_url": media_url(url),
                }
            )
        presentation["kind"] = media_kind
        if kind == 5:
            presentation["body"] = text(info.get("description")) or (
                "【表情】" if url else content or "【表情】"
            )
        else:
            presentation["body"] = "【视频消息】" if kind == 14 else "【图片消息】"
        presentation["media"] = attachments
    elif kind == 1002:
        presentation.update(kind="withdrawn", body=text(info.get("withdraw_hint")) or "消息已撤回")
        if str(info.get("update_type")) == "3" and info.get("msg_id") is not None:
            presentation["withdraw_target"] = str(info["msg_id"])
    elif order:
        presentation["kind"] = "order"
        presentation["body"] = text(info.get("text")) or ("" if safe_url(content) else content)
    elif product:
        presentation["kind"] = "product"
        presentation["body"] = "" if safe_url(content) else content
    elif kind in SYSTEM_TYPES:
        presentation.update(kind="notice", body=text(info.get("mall_content")) or content)
        if kind == 56 and isinstance(info.get("content"), list):
            # Menu entries are readable history, never executable platform actions.
            presentation["body"] = (
                "\n".join(
                    text(x.get("text"), 1000)
                    for x in info["content"][:30]
                    if isinstance(x, dict) and x.get("text")
                )
                or content
            )
    elif kind not in {0, None}:
        presentation.update(
            kind="unsupported",
            title=f"消息类型 {kind}",
            body=text(first(sources, "description", "title", "sub_title"))
            or content
            or "此类消息需在拼多多商家后台查看。",
        )
    quote = message.get("quote_msg")
    if isinstance(quote, dict):
        presentation["quote"] = text(quote.get("content"), 1000) or "引用了一条消息"
    display = presentation["body"]
    summary = display
    if product:
        summary = f"【{product['name']} · 商品 {goods_id}】" + ("\n" + display if display else "")
    if order:
        summary = "【订单信息" + (" " + order_id if order_id else "") + "】\n" + summary
    if presentation["kind"] == "unsupported":
        summary = presentation["title"] + "\n" + display
    identity = message.get("msg_id")
    normalized_role = (
        "system"
        if kind in SYSTEM_TYPES
        else "customer"
        if role == "user"
        else "agent"
        if role in {"mall_cs", "mall"}
        else "system"
    )
    return {
        "platform_id": str(identity) if identity is not None else None,
        "role": normalized_role,
        "sender_role": text(role) or "unknown",
        "content": summary[:20000],
        "created_at": timestamp(message.get("ts") or message.get("time")),
        "type": kind,
        "goods_id": goods_id,
        "attachments": attachments,
        "presentation": presentation,
        "platform_conv_id": text(message.get("conv_id")),
        "chat_type": text(message.get("chat_type")),
    }


def read_presentation(value):
    try:
        result = json.loads(value) if isinstance(value, str) else value
        return result if isinstance(result, dict) else {}
    except (ValueError, TypeError):
        return {}


def message_preview(content, presentation=None):
    p = read_presentation(presentation)
    kind = p.get("kind")
    if kind in {"image", "video", "emotion", "withdrawn"}:
        return {"image": "【图片】", "video": "【视频】", "emotion": "【表情】", "withdrawn": "消息已撤回"}[
            kind
        ]
    product = p.get("product") or {}
    if p.get("order"):
        return ("【订单】" + p["order"].get("id", "") + " " + product.get("name", "")).strip()[:160]
    if product:
        return ("【商品】" + product.get("name", ""))[:160]
    # Old saved summaries included long tracking URLs after a separate first line.
    if str(content).startswith("【商品 "):
        return str(content).split("\n", 1)[0][:160]
    return text(p.get("body") or content, 160)
