"""Readable Markdown transcripts, with literal customer text and safe media links."""

import re
from urllib.parse import quote

from .message_content import media_url, safe_url


def heading(value):
    value = " ".join(str(value or "").split())
    value = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([\\`*_{}\[\]()#+|>~])", r"\\\1", value)


def literal(value):
    text = str(value or "")
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}text\n{text}\n{fence}\n"


def link(value):
    url = safe_url(value)
    return quote(url, safe=":/?=&%#@!$+,;~_-.") if url else ""


def conversation_header(contact, shop_name):
    return (
        f"# {heading(contact['name'])}\n\n"
        f"- 店铺：{heading(shop_name)}\n"
        f"- 买家 ID：{heading(contact['uid'])}\n"
        f"- 聊天类型：{heading(contact['chat_type']) or '默认'}\n\n"
    )


def message_markdown(value, stamp):
    p = value.get("presentation") or {}
    role = {
        "customer": "买家",
        "agent": "客服",
        "assistant": "AI 助手",
        "draft": "AI 草稿（未发送）",
        "system": "系统",
    }.get(value.get("role"), value.get("role") or "未知")
    body = p.get("body", value.get("content", ""))
    result = f"## {heading(stamp)} · {heading(role)}\n\n"
    if value.get("platform_id"):
        result += f"消息 ID：{heading(value['platform_id'])}\n\n"
    if value.get("export_change") == "updated":
        result += "变更：更新（请按消息 ID 替换此前导出记录）。\n\n"
    if p.get("kind") == "withdrawn":
        return result + literal(body or "消息已撤回") + "\n"
    if p.get("quote"):
        result += "引用：\n\n" + literal(p["quote"]) + "\n"
    if body:
        result += literal(body) + "\n"
    product = p.get("product") or {}
    if product:
        result += "### 商品信息\n\n"
        for key, label in (
            ("name", "名称"),
            ("id", "商品 ID"),
            ("price", "价格"),
            ("coupon_price", "券后价"),
            ("spec", "规格"),
        ):
            if product.get(key):
                result += f"- {label}：{heading(product[key])}\n"
        result += "\n"
        if media_url(product.get("image_url")):
            result += f"![商品图片]({link(product['image_url'])})\n\n"
        if link(product.get("url")):
            result += f"[查看商品]({link(product['url'])})\n\n"
    order = p.get("order") or {}
    if order:
        result += "### 订单信息\n\n"
        for key, label in (
            ("id", "订单号"),
            ("spec", "规格"),
            ("status", "订单状态"),
            ("after_sales_status", "售后状态"),
            ("after_sales_type", "售后类型"),
        ):
            if order.get(key):
                result += f"- {label}：{heading(order[key])}\n"
        result += "\n"
    for media in p.get("media") or []:
        url = link(media.get("url"))
        if not url:
            continue
        if media.get("kind") in {"image", "emotion"} and media_url(media.get("url")):
            result += f"![聊天图片]({url})\n\n"
        else:
            result += f"[{'查看视频' if media.get('kind') == 'video' else '查看媒体文件'}]({url})\n\n"
    return result + "---\n\n"
