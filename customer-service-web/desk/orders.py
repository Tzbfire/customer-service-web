"""Read-only, buyer-scoped order pagination across recent and historical endpoints."""

from .message_content import timestamp, text
from .pdd_protocol import ProtocolError

RECENT_PATH = "/latitude/order/userAllOrder"
HISTORY_PATH = "/latitude/order/userHistoryOrder"


def number(value):
    try:
        return int(value) if not isinstance(value, bool) else None
    except (TypeError, ValueError, OverflowError):
        return None


def normalize_order(raw, uid, mall_id, source):
    if not isinstance(raw, dict):
        raise ProtocolError("订单条目格式不正确。")
    buyer = raw.get("uid")
    mall = raw.get("mallId", raw.get("mall_id"))
    if str(buyer) != str(uid) or str(mall) != str(mall_id):
        raise ProtocolError("订单的买家或店铺归属不匹配，已停止处理。")
    sn = raw.get("orderSn") or raw.get("order_sn")
    goods = raw.get("orderGoodsList") or raw
    if not sn or not isinstance(goods, dict):
        raise ProtocolError("订单缺少可核对的订单号或商品。")
    goods_id = goods.get("goodsId", goods.get("goods_id"))
    if goods_id is None:
        raise ProtocolError("订单缺少商品 ID。")
    quantity = number(goods.get("goodsNumber", goods.get("goods_number")))
    if quantity is None or quantity < 0:
        raise ProtocolError("订单购买件数格式不正确。")
    pay = number(raw.get("payStatus", raw.get("pay_status")))
    status = number(raw.get("orderStatus", raw.get("order_status")))
    group = number(raw.get("groupStatus", raw.get("group_status")))
    return {
        "order_sn": str(sn),
        "buyer_uid": str(uid),
        "goods_id": str(goods_id),
        "sku_id": str(goods.get("skuId", goods.get("sku_id")) or ""),
        "name": text(goods.get("goodsName", goods.get("goods_name")), 500),
        "spec": text(goods.get("spec"), 2000),
        "quantity": quantity,
        "ordered_at": timestamp(raw.get("orderTime") or raw.get("createdAt") or raw.get("created_at")),
        "paid_at": timestamp(raw.get("payTime") or raw.get("pay_time")),
        "pay_status": pay,
        "order_status": status,
        "group_status": group,
        "status_text": text(raw.get("orderStatusStr"), 200),
        "paid_purchase": pay == 2 and status == 1 and group in {1, None} and quantity > 0,
        "source": source,
    }


class Orders:
    def __init__(self, store, channels):
        self.store, self.channels = store, channels

    async def buyer(self, shop_id, buyer_uid):
        shop = self.store.one("SELECT platform_shop_id,deleted_at FROM shops WHERE id=?", (shop_id,))
        if not shop or shop["deleted_at"]:
            raise ProtocolError("店铺不存在或已删除。")
        cookies = self.channels.cookies(shop_id)
        orders = {}
        recent_total = older_total = 0
        period = None
        for source, path in [("recent", RECENT_PATH), ("history", HISTORY_PATH)]:
            if source == "history" and older_total == 0:
                break
            page = 1
            seen = set()
            fetched = 0
            source_ids = set()
            while True:
                body = {"uid": str(buyer_uid), "pageNo": page, "pageSize": 10}
                if source == "recent":
                    body["showHistory"] = True
                else:
                    body["historyTotalPeriodCode"] = period
                response = await self.channels.api(cookies, path, body)
                result = response.get("result")
                if not isinstance(result, dict):
                    raise ProtocolError("平台订单列表格式不正确。")
                total = number(result.get("total"))
                if total is None or total < 0:
                    raise ProtocolError("平台订单列表没有可识别的总数。")
                if source == "recent":
                    recent_total = total
                    older_total = number(result.get("historyTotal")) or 0
                    period = result.get("historyTotalPeriodCode")
                batch = result.get("orders")
                if batch is None and total == 0:
                    batch = []
                if not isinstance(batch, list):
                    raise ProtocolError("平台订单列表缺少订单条目。")
                values = [normalize_order(o, buyer_uid, shop["platform_shop_id"], source) for o in batch]
                signature = tuple(o["order_sn"] for o in values)
                if values and signature in seen:
                    raise ProtocolError("订单分页重复，未取得完整订单。")
                seen.add(signature)
                for value in values:
                    # A recent snapshot has richer SKU and payment fields than a duplicate old snapshot.
                    previous = orders.get(value["order_sn"])
                    if not previous or previous["source"] != "recent":
                        orders[value["order_sn"]] = value
                fresh = set(signature) - source_ids
                source_ids.update(signature)
                fetched = len(source_ids)
                if fetched >= total:
                    break
                if not fresh:
                    raise ProtocolError("订单分页没有新增订单，不能判断无订单或无复购。")
                page += 1
                if page > 1000:
                    raise ProtocolError("单个买家订单超过本轮处理上限，已明确停止。")
        return {
            "orders": sorted(orders.values(), key=lambda o: (o["ordered_at"], o["order_sn"])),
            "recent_total": recent_total,
            "history_total": older_total,
            "history_period_code": period,
            "complete": True,
            "scope": "近期订单＋平台当前允许查询的历史订单；不代表无限期历史或无售后。",
        }
