// Pure message rendering shared by live and historical conversations.
const esc = (value) => String(value ?? "").replace(/[&<>"']/g,
  char => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"})[char]);
export function safeURL(value, media = false) {
  if (typeof value !== "string" || /[\u0000-\u001f]/.test(value)) return "";
  try {
    const url = new URL(value);
    if (!["https:", "http:"].includes(url.protocol) || url.username || url.password ||
        (url.port && !["80", "443"].includes(url.port))) return "";
    if (media && (url.protocol !== "https:" || (url.port && url.port !== "443") ||
        !["pddpic.com", "yangkeduo.com", "pinduoduo.com"].some(domain => url.hostname === domain || url.hostname.endsWith("." + domain)))) return "";
    return url.href;
  } catch { return ""; }
}
export function buyerLabel(item) {
  const name = (item.buyer_name || "").trim();
  return name && name !== String(item.buyer_uid) ? name : "买家（昵称未提供）";
}
function linkedText(value) {
  return String(value || "").split(/(https?:\/\/[^\s<>"']+)/g).map(part => {
    const url = safeURL(part);
    return url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(part.length > 80 ? part.slice(0, 65) + "…" : part)}</a>` : esc(part);
  }).join("");
}
function image(url, label = "聊天图片", compact = false) {
  const source = safeURL(url, true);
  if (!source) return "";
  return `<button type="button" class="chat-media message-image ${compact ? "goods-image" : ""} is-loading" data-action="preview-message-image" data-image-url="${esc(source)}" aria-label="放大${esc(label)}"><img src="${esc(source)}" alt="${esc(label)}" loading="lazy" decoding="async" referrerpolicy="no-referrer"><span class="media-loading">加载图片…</span><span class="media-fallback">图片加载失败，点击重试或查看原图</span></button>`;
}
export function productCard(product, order = null, compact = false) {
  if (!product && !order) return "";
  const url = safeURL(product?.url);
  const title = product?.name || "商品信息未提供";
  const field = (label, value) => value ? `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>` : "";
  return `<section class="goods-card ${compact ? "compact" : ""}" aria-label="${order ? "订单信息" : "商品信息"}"><div class="goods-card-label">${order ? "订单信息" : compact ? "当前咨询商品" : "商品咨询"}</div><div class="goods-card-main">${product?.image_url ? image(product.image_url, "商品图片", true) : '<span class="goods-placeholder" aria-hidden="true">商品</span>'}<div class="goods-description"><div class="goods-title">${url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(title)}</a>` : esc(title)}</div>${product?.price ? `<div class="goods-price">${esc(product.price)}${product.coupon_price ? `<small>券后 ${esc(product.coupon_price)}</small>` : ""}</div>` : ""}<dl class="goods-fields">${field("商品 ID", product?.id)}${field("规格", order?.spec || product?.spec)}${field("订单号", order?.id)}${field("订单状态", order?.status)}${field("售后状态", order?.after_sales_status)}${field("售后类型", order?.after_sales_type)}</dl>${url ? `<a class="goods-open" href="${esc(url)}" target="_blank" rel="noopener noreferrer">查看商品 ↗</a>` : ""}</div></div></section>`;
}
export function conversationProduct(messages, goodsId = "") {
  const current = [...messages].reverse().map(m => m.presentation?.product).find(p => p && (!goodsId || p.id === goodsId));
  return current ? `<div class="thread-product">${productCard(current, null, true)}</div>` : "";
}
export function timelineMessage(message, buyerName = "", when = () => "") {
  if (message.role === "draft") return `<div class="draft-box"><div class="flex between"><span class="badge blue">AI 回复草稿 · 未发送</span><span class="filemeta">${when(message.created_at)}</span></div><p>${esc(message.content)}</p><div class="flex between"><span class="filemeta">引用 ${(message.sources || []).length} 份资料片段</span><button class="small" data-action="use-draft" data-id="${message.id}">使用这条草稿</button></div></div>`;
  const p = message.presentation || {};
  if (p.hidden) return "";
  const kind = p.kind || "text";
  const body = p.body ?? message.content;
  if (["notice", "withdrawn"].includes(kind) || message.role === "system") {
    return `<div class="message-notice"><span>${linkedText(body || "系统提示")}</span><time>${message.created_at ? when(message.created_at) : "时间未知"}</time>${p.product ? productCard(p.product) : ""}</div>`;
  }
  const customer = message.role === "customer";
  const author = (customer && buyerName) || message.author || ({customer:"买家", agent:"客服", assistant:"AI 助手"})[message.role] || "系统";
  const status = ({sent:"平台已确认发送", sending:"正在发送", uncertain:"发送结果未确认，请在商家后台核对", error:"需要人工处理", historical:"历史记录"})[message.state];
  let rich = "";
  for (const item of p.media || message.attachments || []) {
    const url = safeURL(item.url);
    if (!url) continue;
    const inline = safeURL(item.inline_url || item.url, true);
    if (inline && ["image", "emotion"].includes(item.kind)) rich += image(inline, item.kind === "emotion" ? "表情" : "图片");
    if (inline && item.kind === "video") rich += `<div class="chat-media message-video"><video controls preload="metadata" playsinline referrerpolicy="no-referrer" src="${esc(inline)}" aria-label="聊天视频"></video><span class="media-fallback">视频无法播放，请打开原视频查看。</span></div>`;
    rich += `<a class="history-attachment" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${item.kind === "video" ? "打开原视频" : "查看原图"} ↗</a>`;
  }
  const mediaOnly = ["image", "video"].includes(kind) && rich;
  const card = productCard(p.product, p.order);
  return `<div class="bubble-row ${customer ? "" : "user"}"><span class="avatar">${customer ? "客" : message.role === "assistant" ? "AI" : "我"}</span><div class="message-body"><div class="bubble-meta">${esc(author)} · ${message.created_at ? when(message.created_at) : "时间未知"}</div>${p.quote ? `<blockquote class="message-quote">${esc(p.quote)}</blockquote>` : ""}${kind === "unsupported" ? `<div class="message-unsupported">${esc(p.title || "暂不支持的消息")}</div>` : ""}${!mediaOnly && body ? `<div class="bubble">${linkedText(body)}</div>` : ""}${rich}${card}${status ? `<div class="message-state ${esc(message.state)}">${status}</div>` : ""}</div></div>`;
}
