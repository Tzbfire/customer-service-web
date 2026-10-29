import { buyerLabel, timelineMessage as renderMessage, conversationProduct, safeURL } from "./messages.js?v=20261029-chat-images";
("use strict");
const $ = (selector, parent = document) => parent.querySelector(selector);
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (char) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        char
      ],
  );
const paths = {
  home: "M3 10 12 3l9 7v10H3z M9 20v-7h6v7",
  chat: "M4 4h16v13H9l-5 4z M8 9h8 M8 13h5",
  book: "M3 4h7l2 2 2-2h7v16h-7l-2 1-2-1H3z M12 6v15",
  spark: "m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z",
  shop: "M4 10v11h16V10 M3 10l2-7h14l2 7 M3 10q3 4 6 0 3 4 6 0 3 4 6 0 M9 21v-7h6v7",
  settings: "M4 7h16 M4 17h16 M8 4v6 M16 14v6",
  users:
    "M16 21v-3q0-4-5-4H7q-5 0-5 4v3 M9 3a4 4 0 1 1 0 8 4 4 0 0 1 0-8 M17 4q5 0 5 4t-5 4 M18 15q4 0 4 5",
  upload: "M12 16V3 m-5 5 5-5 5 5 M4 15v6h16v-6",
  file: "M5 3h9l5 5v13H5z M14 3v6h5 M8 13h8 M8 17h6",
  arrow: "M5 12h14 m-5-5 5 5-5 5",
  check: "m5 12 4 4L19 6",
  logout: "M10 3H4v18h6 M9 12h12 m-5-5 5 5-5 5",
  close: "m6 6 12 12 M6 18 18 6",
  plus: "M12 4v16 M4 12h16",
  clock: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18 M12 7v5l3 2",
  link: "m10 14 4-4 M8 16l-2 2a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0 M14 8l2-2a4 4 0 0 1 6 6l-4 4a4 4 0 0 1-6 0",
};
function icon(name) {
  return `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="${paths[name] || paths.chat}"/></svg>`;
}
const state = {
  user: null,
  page: "home",
  shops: [],
  shopId: null,
  documents: [],
  filter: "all",
  search: "",
  conversationId: null,
  conversationSignature: "",
  history: [],
  testResults: [],
  modal: null,
};
const names = {
  home: "工作概览",
  inbox: "接待工作台",
  knowledge: "知识库",
  playground: "问答测试",
  shops: "店铺连接",
  model: "模型配置",
  team: "团队成员",
};
function toast(message, error = false) {
  const element = $("#toast");
  element.textContent = message;
  element.className = "show" + (error ? " error" : "");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (element.className = ""), 4200);
}
async function api(url, options = {}) {
  const response = await fetch(url, {
    credentials: "same-origin",
    ...options,
    headers: {
      "X-Desk-Request": "1",
      ...(options.body instanceof FormData
        ? {}
        : { "Content-Type": "application/json" }),
      ...options.headers,
    },
  });
  let value;
  try {
    value = await response.json();
  } catch {
    throw new Error("服务器响应异常，请重试。");
  }
  if (!response.ok) {
    if (response.status === 401 && state.user) {
      state.user = null;
      closeModal();
      await init();
    }
    throw new Error(value.detail || "操作失败，请重试。");
  }
  return value;
}
const requestId = () =>
  crypto.randomUUID
    ? crypto.randomUUID()
    : Array.from(crypto.getRandomValues(new Uint8Array(16)), (value) =>
        value.toString(16).padStart(2, "0"),
      ).join("");
const post = (url, body = {}) =>
  api(url, { method: "POST", body: JSON.stringify(body) });
const when = (stamp) =>
  new Date(stamp * 1000).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
function badge(value) {
  const types = {
    parsing: ["识别中", "blue"],
    draft: ["待审核", "orange"],
    ready: ["已入库", "green"],
    failed: ["识别失败", "red"],
    connected: ["已连接", "green"],
    offline: ["未连接", ""],
    connecting: ["连接中", "blue"],
    reconnecting: ["重连中", "orange"],
  };
  const item = types[value] || [value, ""];
  return `<span class="badge ${item[1]}">${esc(item[0])}</span>`;
}
function empty(title, text, button = "") {
  return `<div class="empty"><div class="empty-symbol">${icon("chat")}</div><h3>${esc(title)}</h3><p>${esc(text)}</p>${button}</div>`;
}
function heading(title, subtitle, action = "") {
  return `<div class="page-heading"><div><div class="eyebrow">MERCHANT WORKSPACE</div><h1>${esc(title)}</h1><p>${esc(subtitle)}</p></div>${action}</div>`;
}
function selectedShop() {
  return state.shops.find((shop) => shop.id === state.shopId);
}
function admin() {
  return state.user?.role === "admin";
}
function modal(title, body, wide = false) {
  closeModal();
  state.modal = title;
  $("#modal-root").innerHTML =
    `<div class="overlay"><section class="modal ${wide ? "wide" : ""}" role="dialog" aria-modal="true" aria-label="${esc(title)}"><div class="modal-header"><h2>${esc(title)}</h2><button class="quiet small" data-action="close-modal" aria-label="关闭窗口">${icon("close")}</button></div><div class="modal-body">${body}</div></section></div>`;
}
function closeModal() {
  state.modal = null;
  $("#modal-root").innerHTML = "";
}

async function busy(button, work) {
  if (button?.disabled) return;
  if (button) button.disabled = true;
  try {
    return await work();
  } catch (error) {
    toast(error.message, true);
  } finally {
    if (button) button.disabled = false;
  }
}
async function init() {
  try {
    const status = await api("/api/status");
    if (!status.initialized) return authPage(true);
    try {
      state.user = await api("/api/me");
    } catch {
      return authPage(false);
    }
    await loadShops();
    shell();
    await renderPage();
  } catch (error) {
    $("#app").innerHTML = empty("暂时无法连接服务", error.message);
  }
}
function authPage(setup) {
  $("#app").innerHTML =
    `<div class="auth"><section class="auth-story"><div class="brand"><img src="/static/mark.svg" alt="知店"><div><strong>知店</strong><small>MERCHANT DESK</small></div></div><div class="eyebrow">YOUR KNOWLEDGE. BETTER CONVERSATIONS.</div><h1>把商品知识，<br>变成每一次好回答。</h1><p>连接店铺与大模型，让团队在同一个工作台里接待客户，让回答有据可查。</p><div class="auth-tag">商品知识库 / AI 辅助接待 / 团队协作</div></section><section class="auth-form"><form id="auth-form"><h2>${setup ? "创建你的工作台" : "欢迎回来"}</h2><p>${setup ? "首次使用，先创建管理员账号。" : "登录后继续处理店铺咨询。"}</p><div class="field"><label for="username">${setup ? "管理员用户名" : "用户名"}</label><input id="username" name="username" autocomplete="username" required maxlength="40" placeholder="请输入用户名"></div><div class="field"><label for="password">密码</label><input id="password" name="password" type="password" autocomplete="${setup ? "new-password" : "current-password"}" required minlength="${setup ? 10 : 1}" maxlength="200" placeholder="${setup ? "至少 10 位，请使用独立密码" : "请输入密码"}"></div><button type="submit" class="primary">${setup ? "创建并进入工作台" : "登录工作台"}${icon("arrow")}</button><div id="auth-error" class="auth-error" role="alert"></div></form></section></div>`;
  $("#auth-form").addEventListener("submit", (event) => {
    event.preventDefault();
    busy($("button[type=submit]", event.target), async () => {
      try {
        state.user = await post(
          setup ? "/api/setup" : "/api/login",
          Object.fromEntries(new FormData(event.target)),
        );
        await loadShops();
        shell();
        await renderPage();
      } catch (error) {
        $("#auth-error").textContent = error.message;
      }
    });
  });
}
async function loadShops() {
  state.shops = await api("/api/shops");
  if (!state.shops.some((shop) => shop.id === state.shopId))
    state.shopId = state.shops[0]?.id || null;
}
function shell() {
  const nav = (page, type) =>
    `<button data-page="${page}" class="${state.page === page ? "active" : ""}">${icon(type)}<span>${names[page]}</span></button>`;
  $("#app").innerHTML =
    `<div class="layout"><aside class="sidebar"><div class="brand"><img src="/static/mark.svg" alt=""><div><strong>知店</strong><small>MERCHANT DESK</small></div></div><div class="nav-caption">WORKSPACE</div><nav class="nav" aria-label="工作台导航">${nav("home", "home")}${nav("inbox", "chat")}${nav("knowledge", "book")}${nav("playground", "spark")}</nav>${admin() ? `<div class="nav-caption">MANAGEMENT</div><nav class="nav" aria-label="管理导航">${nav("shops", "shop")}${nav("model", "settings")}${nav("team", "users")}</nav>` : ""}<div class="side-note"><span class="dot green"></span>你的团队，同一个工作台<br>资料先审核，回答有依据。</div></aside><main class="main"><header class="topbar"><div class="shop-picker"><span class="shop-icon">${icon("shop")}</span><select id="global-shop" aria-label="当前店铺">${state.shops.map((shop) => `<option value="${shop.id}" ${shop.id === state.shopId ? "selected" : ""}>${esc(shop.name)}</option>`).join("")}</select></div><div class="top-right"><span class="muted smalltext top-status">店铺工作空间</span><span class="top-separator"></span><span class="avatar">${esc(state.user.username.slice(0, 1).toUpperCase())}</span><div class="user-info"><div class="smalltext">${esc(state.user.username)}</div><span class="muted smalltext">${admin() ? "管理员" : "客服成员"}</span></div><button class="quiet small" data-action="logout" aria-label="退出登录">${icon("logout")}</button></div></header><div id="page" class="page"></div></main></div>`;
  $("#global-shop").addEventListener("change", async (event) => {
    state.shopId = Number(event.target.value);
    state.history = [];
    state.testResults = [];
    state.conversationId = null;
    state.conversationSignature = "";
    await renderPage();
  });
}
async function navigate(page) {
  state.page = page;
  document
    .querySelectorAll("[data-page]")
    .forEach((button) =>
      button.classList.toggle("active", button.dataset.page === page),
    );
  await renderPage();
}
async function renderPage() {
  const page = state.page;
  $("#page").innerHTML =
    '<div class="empty"><span class="spinner"></span></div>';
  try {
    if (!state.shopId && !["shops", "team", "model"].includes(page)) {
      $("#page").innerHTML = empty(
        "先创建一个店铺",
        "在店铺连接中建立你的店铺工作空间。",
      );
      return;
    }
    await {
      home: renderHome,
      knowledge: renderKnowledge,
      playground: renderPlayground,
      inbox: renderInbox,
      shops: renderShops,
      model: renderModel,
      team: renderTeam,
    }[page]();
  } catch (error) {
    toast(error.message, true);
    if ($("#page")) $("#page").innerHTML = empty("页面加载失败", error.message);
  }
}
async function renderHome() {
  const summary = await api("/api/summary?shop_id=" + state.shopId);
  const shop = selectedShop();
  const stats = [
    ["会话总数", summary.conversations, "已接收并保存的客户会话", "chat"],
    ["等待人工", summary.waiting, "需要团队跟进的会话", "users"],
    ["已入库资料", summary.ready_documents, "AI 可以检索的审核后资料", "book"],
    ["待审核资料", summary.draft_documents, "确认识别内容后即可入库", "clock"],
  ];
  const steps = [
    [
      "配置大模型",
      "填入你已有的接口地址、API Key 和模型名称。",
      summary.model_configured,
      "model",
    ],
    [
      "建立商品知识库",
      "上传商品资料，核对识别结果并确认入库。",
      summary.ready_documents > 0,
      "knowledge",
    ],
    [
      "测试回答效果",
      "先用真实买家问题检查答案和引用资料。",
      false,
      "playground",
    ],
    [
      "连接拼多多店铺",
      "完成商家登录后，先从 AI 草稿模式开始接待。",
      shop?.connection.status === "connected",
      "shops",
    ],
  ];
  $("#page").innerHTML =
    heading("工作概览", "掌握接待状态，让每一次回复更有把握。") +
    `<div class="stats">${stats.map((item) => `<section class="stat"><div class="stat-label">${item[0]}${icon(item[3])}</div><div class="stat-number">${item[1]}</div><div class="stat-foot">${item[2]}</div></section>`).join("")}</div><section class="hero"><div><div class="eyebrow">KNOWLEDGE FIRST</div><h2>让 AI 先读懂你的商品。</h2><p>图片、文档、表格，都可以成为店铺的专属资料。确认知识后，先在问答测试中检查回答，再交给 AI 辅助接待。</p><div class="gap"></div><button class="primary" data-page="knowledge">管理商品资料${icon("arrow")}</button></div><div class="hero-mark">${icon("book")}</div></section><div class="grid2"><section class="panel"><div class="panel-head"><h2>开始接待前，完成这几步</h2><span class="badge blue">快速开始</span></div><div class="panel-body steps">${steps.map((item, index) => `<div class="step ${item[2] ? "done" : ""}"><span class="step-n">${item[2] ? "✓" : String(index + 1).padStart(2, "0")}</span><div class="step-content"><h3>${item[0]}</h3><p>${item[1]}</p></div>${admin() || !["model", "shops"].includes(item[3]) ? `<button class="quiet small" data-page="${item[3]}" aria-label="前往${item[0]}">${icon("arrow")}</button>` : ""}</div>`).join("")}</div></section><section class="panel"><div class="panel-head"><h2>当前店铺</h2>${badge(shop?.connection.status || "offline")}</div><div class="panel-body"><div class="flex"><span class="avatar">${icon("shop")}</span><div><h3>${esc(shop?.name)}</h3><p class="muted smalltext">${shop?.platform_shop_id ? "拼多多店铺 " + esc(shop.platform_shop_id) : "尚未绑定拼多多账号"}</p></div></div><div class="gap"></div><div class="notice">${esc(shop?.connection.detail || "尚未连接消息服务。")}<br>当前接待模式：${{ review: "AI 生成草稿，由客服审核发送", manual: "人工接待", auto: "有资料依据时自动回复", ai_takeover: "AI 接管，新文字咨询由模型直接接待" }[shop?.mode] || "AI 草稿"}</div><div class="gap"></div><h3>资料使用方式</h3><p class="muted smalltext">商品资料请填写对应的拼多多商品 ID。发货、退换货等通用政策可不填商品 ID。不同店铺的资料分别检索。</p><div class="gap"></div><button data-page="inbox">进入接待工作台${icon("arrow")}</button></div></section></div>`;
}
async function renderKnowledge() {
  state.documents = await api("/api/documents?shop_id=" + state.shopId);
  $("#page").innerHTML =
    heading(
      "店铺知识库",
      "把你已有的商品资料，整理成 AI 回答的依据。",
      admin()
        ? '<div class="flex"><button class="primary" data-action="open-upload">' +
            icon("plus") +
            ' 上传资料</button><button data-action="sync-products">同步店铺商品并入库</button></div>'
        : "",
    ) +
    `<section class="panel product-library"><div class="panel-head"><div><h2>店铺商品</h2><p class="filemeta">商品名称、规格与详情同步到本地，按商品 ID 供 AI 检索。详情图文字需识别后审核。</p></div></div><div id="product-catalog" class="panel-body">正在读取本地商品…</div></section><div class="gap"></div><section class="panel"><div class="panel-head"><h2>资料库 <span id="knowledge-document-count" class="muted smalltext">${state.documents.length} 份资料</span></h2><span class="badge blue">按当前店铺检索</span></div><div class="filters"><button data-filter="all" class="selected">全部资料</button><button data-filter="ready">已入库</button><button data-filter="draft">待审核</button><input id="document-search" aria-label="搜索资料" placeholder="搜索标题或商品 ID"></div><div id="document-list"></div></section><div class="gap"></div><div class="grid2"><div class="notice"><strong>商品资料</strong><br>适合规格、尺寸、材质、使用方法和注意事项。请关联商品 ID，避免不同商品的知识混用。</div><div class="notice"><strong>通用政策</strong><br>适合发货说明、退换货政策和店铺常见问答。作为本店所有商品的共同参考。</div></div>`;
  state.filter = "all";
  state.search = "";
  drawDocuments();
  await refreshProductCatalog();
  $("#document-search").addEventListener("input", (event) => {
    state.search = event.target.value;
    drawDocuments();
  });
}
async function refreshProductCatalog() {
  const shopId=state.shopId;
  const result=await api("/api/products?shop_id="+shopId);
  if(state.page!=="knowledge" || state.shopId!==shopId || !$("#product-catalog")) return;
  state.productSync=result.sync;
  state.catalogProducts=result.items;
  const sync=result.sync;
  const labels={running:"正在同步商品",done:"商品同步完成",partial:"部分商品同步失败",failed:"商品同步失败",interrupted:"商品同步中断"};
  const progress=sync ? `<div class="notice product-sync-progress"><strong>${esc(labels[sync.status] || sync.status)}</strong> · 已入库 ${sync.completed}/${sync.total} 件${sync.failed ? " · 失败 "+sync.failed+" 件" : ""}${sync.error ? `<p class="inline-error">${esc(sync.error)}</p>` : ""}</div>` : "";
  $("#product-catalog").innerHTML=progress+(result.items.length ? `<div class="catalog-grid">${result.items.map(p=>`<article class="catalog-item">${safeURL(p.thumb_url,true)?`<img class="catalog-thumb" src="${esc(safeURL(p.thumb_url,true))}" alt="商品缩略图" loading="lazy" referrerpolicy="no-referrer">`:'<span class="catalog-thumb goods-placeholder">商品</span>'}<div class="catalog-info"><strong>${esc(p.name)}</strong><div class="filemeta">商品 ID：${esc(p.goods_id)} · ${p.sku_count} 个在售 SKU · ${p.image_count} 张详情图</div><div class="filemeta">${p.price_min?esc(p.price_min+(p.price_max&&p.price_max!==p.price_min?" — "+p.price_max:""))+" · 同步参考价 · " : ""}${p.available ? p.knowledge_state === "ready" ? "商品事实已入库" : "商品事实未入库" : "本次在售列表未返回，知识已停用"}</div>${p.error?`<p class="inline-error">${esc(p.error)}</p>`:""}<div class="flex catalog-actions"><button class="quiet small" data-action="view-product" data-id="${esc(p.goods_id)}">查看本地商品</button>${p.document_id?`<button class="quiet small" data-action="view-document" data-id="${esc(p.document_id)}">查看商品知识</button>`:""}${admin() && p.available && p.image_count?`<button class="small" data-action="product-images" data-id="${esc(p.goods_id)}" ${p.image_state==="running"?"disabled":""}>${p.image_state==="running"?"正在识别详情图…":"识别详情图"}</button>`:""}${p.image_document_id?`<button class="small" data-action="view-document" data-id="${esc(p.image_document_id)}">${p.image_knowledge_state==="ready"?"查看图片知识":"审核图片知识"}</button>`:""}</div>${p.image_error?`<p class="inline-error">${esc(p.image_error)}</p>`:""}${admin()?`<div class="learning-controls"><button class="small" data-action="learning-preview" data-id="${esc(p.goods_id)}" ${p.learning?.status==="running"?"disabled":""}>聊天＋订单总结</button>${learningStatusHTML(p)}</div>`:""}</div></article>`).join("")}</div>` : empty("尚未同步商品","管理员可以从当前店铺拉取在售商品，自动建立按商品 ID 区分的知识。"));
}
function learningStatusHTML(product) {
  const j=product.learning;
  if(!j)return "";
  const status={running:"正在整理",done:"已生成待审核总结",partial:"已生成，部分订单未知",failed:"整理未完成",interrupted:"任务已暂停"}[j.status]||j.status;
  const phase={orders:"拉取近期与历史订单",model:"模型整理咨询",write:"写入待审核知识",done:"可审核"}[j.phase]||j.phase;
  return `<div class="filemeta learning-progress">${esc(status)} · ${esc(phase)}<br>${j.completed}/${j.total} 个案例 · 已完成 ${j.model_batches} 批模型整理${j.failed?" · "+j.failed+" 个订单查询未完成":""}</div>${j.error?`<div class="inline-error">${esc(j.error)}</div>`:""}<div class="flex catalog-actions"><button class="quiet small" data-action="learning-evidence" data-job="${j.id}">查看案例与来源</button>${j.document_id?`<button class="small" data-action="view-document" data-id="${j.document_id}">${j.knowledge_state==="ready"?"查看总结知识":"审核总结知识"}</button>`:""}${j.status==="running"?`<button class="quiet small" data-action="learning-cancel" data-job="${j.id}">暂停总结</button>`:["failed","interrupted"].includes(j.status)?`<button class="small" data-action="learning-resume" data-job="${j.id}">继续总结</button>`:""}</div>`;
}
async function openLearningPreview(goodsId) {
  const preview=await api(`/api/shops/${state.shopId}/products/${encodeURIComponent(goodsId)}/learning-preview`);
  modal("聊天＋订单总结",`<p>此商品在 ${when(preview.snapshot_at)} 的聊天快照中关联 <strong>${preview.cases} 个咨询案例</strong>。</p><div class="notice">会读取这些买家的近期和历史订单，按当前商品核对规格、购买件数、咨询前后下单及复购。订单数据只保留必要交易字段；提交给模型的材料会隐藏买家和订单编号，并尽量隐藏联系信息。<br>预计至少 ${preview.estimated_batches} 批文字模型调用；长案例可能增加批次。结果先生成待审核资料，不自动入库，不向买家发消息。<br>当前快照可能缺少最新消息；如需最新范围，请先重新导出全部历史。${preview.truncated_cases?`<br>${preview.truncated_cases} 个长案例将选取开头和结尾片段，并标明范围限制。`:""}</div><div class="gap"></div><div class="notice">本次只生成商品知识总结，不会改变客服行为。不根据购买数量推算通用施工耗量。</div><div class="form-actions"><button data-action="close-modal">取消</button><button class="primary" data-action="learning-start" data-id="${esc(goodsId)}" ${preview.cases?"":"disabled"}>生成总结（待审核）</button></div>`);
}
async function showLearningEvidence(jobId,page=1) {
  const result=await api(`/api/shops/${state.shopId}/learning/${jobId}?page=${page}`);
  const label={customer:"买家",agent:"客服"};
  const cases=result.cases.map(c=>`<section class="learning-case"><h3>${esc(c.case_id)} · 买家 ID ${esc(c.buyer_uid)}</h3><p>${esc(c.analysis.discussion||"尚未完成模型总结")}</p><p>面积原话：${esc(c.analysis.area_quote||"未提取到可靠原文")}${c.analysis.area_message_ref?" · 来源 "+esc(c.analysis.area_message_ref):""}</p>${c.purchases.known?`<p>该商品订单 ${c.purchases.matching_orders} 笔 · 已支付未取消 ${c.purchases.paid_orders} 笔 · 历史多笔购买：${c.purchases.repeat_ever?"是":"当前可见范围未观察到"}</p><p>咨询时间基准：${esc(c.purchases.consultation_basis)} · ${c.purchases.consultation_at?when(c.purchases.consultation_at):"未知"}<br>基准后支付订单 ${c.purchases.paid_after_consultation===null?"未知":c.purchases.paid_after_consultation+" 笔"} · 基准后再次下单：${c.purchases.repeat_after_consultation===null?"未知":c.purchases.repeat_after_consultation?"是":"未观察到"}</p><div class="table-wrap"><table><thead><tr><th>订单引用</th><th>下单时间</th><th>规格</th><th>件数</th><th>支付判断</th></tr></thead><tbody>${c.purchases.timeline.map(o=>`<tr><td>${esc(o.ref)} · ${o.source==="history"?"历史":"近期"}</td><td>${o.ordered_at?when(o.ordered_at):"未知"}</td><td>${esc(o.spec||o.sku_id||"未提供")}</td><td>${o.quantity}</td><td>${o.paid_purchase?"已支付未取消":"其他状态"} (${o.pay_status})</td></tr>`).join("")}</tbody></table></div>`:`<p class="inline-error">订单未完整获取，购买及复购未知。${esc(c.error)}</p>`}<details><summary>查看对应商品的聊天来源（${c.messages.length} 条${c.chat_truncated?"，已截取":""}）</summary>${c.messages.map(m=>`<div class="learning-source"><span class="filemeta">${esc(m.ref)} · ${esc(label[m.role]||m.role)} · ${m.at?when(m.at):"时间未知"}${m.platform_id?" · 平台消息 "+esc(m.platform_id):""}</span><p>${esc(m.content)}</p></div>`).join("")}</details></section>`).join("");
  modal("总结案例与来源",`<div class="notice">以下订单结果按程序计算，原文与引用用于人工核对。历史购买不等于本轮咨询后的复购；不能由件数直接推算覆盖面积。</div>${cases||empty("暂无案例","请先运行总结任务。")}<div class="form-actions">${page>1?`<button data-action="learning-evidence" data-job="${jobId}" data-page-number="${page-1}">上一页</button>`:""}<span>第 ${page} 页</span>${result.has_more?`<button data-action="learning-evidence" data-job="${jobId}" data-page-number="${page+1}">下一页</button>`:""}</div>`,true);
}
async function showBuyerOrders() {
  const c=state.inboxSource==="history"?state.historySelected:state.activeConversation;
  if(!c)return;
  modal("买家订单",'<div class="notice">正在读取近期与历史订单…</div>',true);
  try {
    const result=await api(`/api/shops/${state.shopId}/buyers/${encodeURIComponent(c.buyer_uid)}/orders`);
    if(state.modal!=="买家订单")return;
    const paid=result.orders.filter(o=>o.paid_purchase);
    modal("买家订单",`<div class="notice">已读取近期 ${result.recent_total} 笔＋历史 ${result.history_total} 笔，去重 ${result.orders.length} 笔。按不同订单号核对复购，件数按规格分别保留。已支付未取消不代表无售后；历史范围受平台和账号权限限制。</div><div class="table-wrap"><table><thead><tr><th>下单时间 / 订单</th><th>商品 / SKU</th><th>规格与数量</th><th>支付及订单状态</th></tr></thead><tbody>${result.orders.map(o=>`<tr><td>${o.ordered_at?when(o.ordered_at):"时间未知"}<div class="filemeta">${esc(o.order_sn)} · ${o.source==="history"?"历史订单":"近期订单"}</div></td><td>${esc(o.name)}<div class="filemeta">商品 ${esc(o.goods_id)}${o.sku_id?" · SKU "+esc(o.sku_id):""}</div></td><td>${esc(o.spec||"规格未提供")}<br><strong>${o.quantity} 件</strong></td><td>${o.paid_purchase?"已支付未取消":"其他状态，需核对"}<div class="filemeta">支付 ${o.pay_status} · 订单 ${o.order_status} ${esc(o.status_text)}</div></td></tr>`).join("")}</tbody></table></div>${!result.orders.length?empty("未返回订单","仅代表当前可查询范围没有订单。"):""}<p class="filemeta">已支付未取消的可见订单 ${paid.length} 笔。不同商品不能混为同款复购。</p>`,true);
  } catch(error) {
    if(state.modal==="买家订单") modal("买家订单",`<div class="inline-error">${esc(error.message)}</div><p>订单未获取完整，不能判断未购买或无复购。</p>`,true);
  }
}
async function viewProduct(goodsId) {
  const result=await api(`/api/shops/${state.shopId}/products/${encodeURIComponent(goodsId)}`);
  const p=result.payload;
  modal("本地商品资料",`<h3>${esc(p.name)}</h3><p class="filemeta">商品 ID：${esc(p.goods_id)} · 同步于 ${when(result.updated_at)}</p>${p.description?`<p>${esc(p.description)}</p>`:""}${p.warm_tips?`<p>${esc(p.warm_tips)}</p>`:""}<p class="filemeta">分类：${esc(p.categories.join(" > "))}</p><div class="table-wrap"><table><thead><tr><th>SKU</th><th>规格</th><th>参考拼单价</th><th>状态</th></tr></thead><tbody>${p.skus.map(s=>`<tr><td>${esc(s.id)}</td><td>${esc(s.spec)}</td><td>${esc(s.price)}</td><td>${s.on_sale?"在售":"非在售"}</td></tr>`).join("")}</tbody></table></div><h3>商品详情图</h3><div class="product-gallery">${p.detail_images.filter(url=>safeURL(url,true)).map(url=>`<button type="button" class="chat-media message-image" data-action="preview-message-image" data-image-url="${esc(safeURL(url,true))}"><img src="${esc(safeURL(url,true))}" alt="商品详情图" loading="lazy" referrerpolicy="no-referrer"><span class="media-fallback">图片加载失败</span></button>`).join("")}</div><div class="notice">只有已入库的文字与规格参与 AI 检索，详情图需点击“识别详情图”后审核入库。价格和库存以平台实时页面为准。</div>`,true);
}
function openProductSync() {
  modal("同步店铺商品并入库",`<p>从当前店铺读取在售商品列表、详情和 SKU，保存到本地并建立商品知识。</p><div class="notice">同步专属资料会随平台更新；手工上传的资料和人工编辑过的商品知识会保留。列表不再返回的旧商品，其同步知识会停用。<br>本次只同步结构化文字和图片地址，不调用大模型。详情图识别可随后单独执行。</div><div class="form-actions"><button data-action="close-modal">取消</button><button class="primary" data-action="confirm-product-sync">同步并入库</button></div>`);
}
function openProductImages(goodsId) {
  const p=(state.catalogProducts||[]).find(p=>p.goods_id===goodsId);
  if(!p)return;
  modal("识别商品详情图",`<p>${esc(p.name)}</p><div class="notice">将读取此商品的 ${p.image_count} 张详情图，并发送到你已配置的看图模型进行文字识别，可能产生多次 API 调用和费用。结果先进入待审核区，确认后才用于 AI 回答。失败重试会复用已完成图片。</div><div class="form-actions"><button data-action="close-modal">取消</button><button class="primary" data-action="confirm-product-images" data-id="${esc(goodsId)}">开始识别</button></div>`);
}
function drawDocuments() {
  if (!$("#document-list")) return;
  if ($("#knowledge-document-count")) $("#knowledge-document-count").textContent = `${state.documents.length} 份资料`;
  const items = state.documents.filter(
    (item) =>
      (state.filter === "all" || item.state === state.filter) &&
      `${item.title} ${item.goods_id}`
        .toLowerCase()
        .includes(state.search.toLowerCase()),
  );
  $("#document-list").innerHTML = items.length
    ? `<div class="table-wrap"><table><thead><tr><th>资料名称</th><th>适用范围</th><th>状态</th><th>更新时间</th><th>操作</th></tr></thead><tbody>${items.map((item) => `<tr><td><div class="file-row"><span class="file-icon">${icon("file")}</span><div><div class="filename">${esc(item.title)}</div><div class="filemeta">${esc(item.filename)} · ${item.length.toLocaleString()} 字${item.error ? " · " + esc(item.error) : ""}</div></div></div></td><td>${item.goods_id ? "商品 " + esc(item.goods_id) : "全店通用"}<div class="filemeta">${item.category === "product" ? "商品资料" : "通用政策"}</div></td><td>${badge(item.state)}</td><td class="muted">${when(item.created_at)}</td><td><button class="quiet small" data-action="view-document" data-id="${item.id}">${item.state === "draft" && admin() ? "审核入库" : "查看资料"}</button>${item.state === "failed" && admin() ? `<button class="small" data-action="retry-document" data-id="${item.id}">重试</button>` : ""}</td></tr>`).join("")}</tbody></table></div>`
    : empty(
        "还没有" + (state.filter === "draft" ? "待审核" : "") + "资料",
        "上传你的商品资料，识别完成后可预览、修改并确认入库。",
        admin()
          ? '<button data-action="open-upload">' +
              icon("upload") +
              " 上传第一份资料</button>"
          : "",
      );
}
function openUpload() {
  modal(
    "上传店铺资料",
    `<form id="upload-form"><div class="upload-zone" id="drop-zone">${icon("upload")}<h3>选择或拖入资料文件</h3><p>图片 / PDF / Word / Excel / CSV / TXT / Markdown<br>每个文件最多 25 MB，可一次选择多份。</p><input id="upload-files" name="files" type="file" multiple accept=".png,.jpg,.jpeg,.webp,.pdf,.docx,.xlsx,.csv,.txt,.md" required aria-label="选择资料文件"></div><div class="upload-options"><div class="field"><label for="upload-category">资料类型</label><select id="upload-category" name="category"><option value="product">商品资料</option><option value="policy">通用政策</option></select></div><div class="field"><label for="upload-goods">商品 ID（选填）</label><input id="upload-goods" name="goods_id" maxlength="80" placeholder="拼多多商品 ID"></div></div><div class="gap"></div><div class="notice">图片和扫描件会发送到你配置的看图模型进行识别。长图会分段识别，可能产生多次模型调用。结果先进入待审核区，确认后才会用于回答。</div><div id="upload-progress" class="modal-message"></div><div class="form-actions"><button type="button" data-action="close-modal">取消</button><button type="submit" class="primary">开始上传${icon("arrow")}</button></div></form>`,
  );
  const zone = $("#drop-zone");
  zone.addEventListener("dragover", (event) => {
    event.preventDefault();
    zone.classList.add("over");
  });
  zone.addEventListener("dragleave", () => zone.classList.remove("over"));
  zone.addEventListener("drop", (event) => {
    event.preventDefault();
    zone.classList.remove("over");
    $("#upload-files").files = event.dataTransfer.files;
  });
  $("#upload-form").addEventListener("submit", (event) => {
    event.preventDefault();
    busy($("button[type=submit]", event.target), async () => {
      const files = [...$("#upload-files").files];
      const goods = $("#upload-goods").value,
        category = $("#upload-category").value;
      let success = 0;
      for (const file of files) {
        if ($("#upload-progress"))
          $("#upload-progress").textContent =
            `正在上传 ${file.name}（${success + 1}/${files.length}）`;
        const form = new FormData();
        form.set("shop_id", state.shopId);
        form.set("goods_id", goods);
        form.set("category", category);
        form.set("file", file);
        try {
          await api("/api/documents", { method: "POST", body: form });
          success++;
        } catch (error) {
          toast(`${file.name}：${error.message}`, true);
        }
      }
      closeModal();
      toast(`已上传 ${success} 份资料，识别结果可在资料列表中查看。`);
      await renderKnowledge();
    });
  });
}
async function viewDocument(id) {
  const item = await api("/api/documents/" + id);
  modal(
    admin() ? "核对资料并确认入库" : "查看知识来源",
    `<form id="document-form"><div class="flex between"><span class="muted smalltext">${esc(item.filename)} · ${badge(item.state)}</span><a href="/api/documents/${id}/file" target="_blank" rel="noopener">下载原文件</a></div><div class="gap"></div><div class="field"><label for="doc-title">资料标题</label><input id="doc-title" name="title" value="${esc(item.title)}" required maxlength="200" ${admin() ? "" : "readonly"}></div><div class="grid2"><div class="field"><label for="doc-goods">适用商品 ID</label><input id="doc-goods" name="goods_id" value="${esc(item.goods_id)}" maxlength="80" placeholder="留空表示全店通用" ${admin() ? "" : "readonly"}></div><div class="field"><label for="doc-category">资料类型</label><select id="doc-category" name="category" ${admin() ? "" : "disabled"}><option value="product" ${item.category === "product" ? "selected" : ""}>商品资料</option><option value="policy" ${item.category === "policy" ? "selected" : ""}>通用政策</option></select></div></div><div class="field"><label for="doc-content">${item.state === "parsing" ? "正在识别，请稍后关闭并重新查看" : "识别内容 · 请核对数字、单位和适用条件"}</label><textarea id="doc-content" name="content" class="document-content" required maxlength="600000" ${admin() ? "" : "readonly"}>${esc(item.content)}</textarea>${item.error ? `<div class="inline-error">${esc(item.error)}</div>` : ""}</div>${admin() ? `<div class="form-actions"><button type="button" class="danger" data-action="delete-document" data-id="${id}" ${item.state === "parsing" ? "disabled" : ""}>删除资料</button><button type="submit" class="primary" ${item.state === "parsing" ? "disabled" : ""}>${item.state === "ready" ? "保存并更新知识库" : "确认内容并入库"}${icon("check")}</button></div>` : ""}</form>`,
    true,
  );
  $("#document-form").addEventListener("submit", (event) => {
    event.preventDefault();
    busy($("button[type=submit]", event.target), async () => {
      await post(
        `/api/documents/${id}/publish`,
        Object.fromEntries(new FormData(event.target)),
      );
      closeModal();
      toast("资料已入库，AI 现在可以检索。");
      if (state.page === "knowledge") await renderKnowledge();
    });
  });
}
function renderPlayground() {
  state.testResults = [];
  state.history = [];
  $("#page").innerHTML =
    heading("问答测试", "先用买家的真实问题，检查回答内容与引用依据。") +
    `<div class="playground"><section class="panel chat-panel"><div class="panel-head"><h2 class="flex">${icon("spark")} 店铺 AI 助手</h2><span class="badge blue">仅测试，不发送给买家</span></div><div id="test-messages" class="chat-messages">${empty("从一个商品问题开始", "选择商品范围，输入买家可能提出的问题。")}</div><form id="test-form" class="chat-compose"><div class="field"><label for="test-goods">商品 ID（选填）</label><input id="test-goods" placeholder="填写后，仅检索该商品及全店通用资料" maxlength="80"></div><textarea id="test-question" aria-label="买家问题" placeholder="例如：这款商品的材质是什么？应该怎么清洗？" required maxlength="4000"></textarea><div class="compose-footer"><span>回答基于已审核资料，缺少依据时交给人工。</span><button class="primary" type="submit">测试回答${icon("arrow")}</button></div></form></section><aside class="panel"><div class="panel-head"><h3>本次回答依据</h3>${icon("book")}</div><div id="test-sources" class="sources"><div class="muted smalltext">提问后，这里会显示 AI 引用的资料片段。</div></div></aside></div>`;
  $("#test-form").addEventListener("submit", (event) => {
    event.preventDefault();
    busy($("button[type=submit]", event.target), async () => {
      const question = $("#test-question").value.trim();
      if (!question) return;
      const goods = $("#test-goods").value.trim();
      if (state.testGoods !== goods) {
        state.history = [];
        state.testGoods = goods;
      }
      const list = $("#test-messages");
      if (!state.testResults.length) list.innerHTML = "";
      list.insertAdjacentHTML(
        "beforeend",
        `<div class="bubble-row user"><span class="avatar">你</span><div class="bubble">${esc(question)}</div></div><div class="bubble-row" id="answer-loading"><span class="avatar">AI</span><div class="bubble"><span class="spinner"></span> 正在检索资料并生成回答…</div></div>`,
      );
      list.scrollTop = list.scrollHeight;
      try {
        const result = await post("/api/playground", {
          shop_id: state.shopId,
          question,
          goods_id: goods,
          history: state.history,
        });
        $("#answer-loading")?.remove();
        list.insertAdjacentHTML(
          "beforeend",
          `<div class="bubble-row"><span class="avatar">AI</span><div><div class="bubble-meta">${result.needs_human ? "建议人工确认" : "已引用知识库资料"}</div><div class="bubble">${esc(result.answer)}</div></div></div>`,
        );
        if (result.goods_id && $("#test-goods")) {
          $("#test-goods").value=result.goods_id;
          state.testGoods=result.goods_id;
        }
        state.history.push(
          { role: "user", content: question },
          { role: "assistant", content: result.answer },
        );
        state.history = state.history.slice(-10);
        state.testResults.push(result);
        if ($("#test-sources"))
          $("#test-sources").innerHTML =
            `<div class="notice ${result.needs_human ? "orange" : ""}">${result.retrieval_mode === "model_context" ? "模型理解模式 · 已调用模型" : result.model_called ? "本地关键词模式 · 已调用模型" : "本地关键词模式 · 未调用模型"}<br>${esc(result.reason || "回答已生成。")}</div>` +
            sourceCards(result.sources);
        if ($("#test-question")) $("#test-question").value = "";
      } catch (error) {
        $("#answer-loading")?.remove();
        throw error;
      } finally {
        list.scrollTop = list.scrollHeight;
      }
    });
  });
}
function sourceCards(sources) {
  if (!sources.length)
    return '<div class="source-item muted smalltext">没有可引用的已入库资料。</div>';
  return sources
    .map(
      (item, index) =>
        `<div class="source-item"><div class="source-label">SOURCE ${String(index + 1).padStart(2, "0")} · 片段 ${item.position}</div><button class="source-title" data-action="view-document" data-id="${item.document_id}">${esc(item.title)}</button><p class="source-snippet">${esc(item.content.slice(0, 400))}</p></div>`,
    )
    .join("");
}
function exportStatusHTML(shop) {
  const job = shop.export;
  if (!job) return "";
  const labels = {running:"正在导出",done:"导出完成",failed:"导出未完成",cancelled:"导出已暂停",interrupted:"导出已中断"};
  const phase = {discover:"读取会话列表",messages:"读取聊天记录",package:"生成压缩包",done:"可以下载"}[job.phase] || "准备导出";
  const incremental = job.mode === "incremental";
  const mode = incremental ? (job.baseline_id ? "增量导出" : "首次增量（全量建立基准）") : "全量导出";
  const baseline = job.baseline_at ? ` · 上次成功导出 ${when(job.baseline_at)}` : "";
  const changes = job.status === "done" && incremental ? ` · 新增 ${job.new_messages || 0} 条 / 更新 ${job.updated_messages || 0} 条` : "";
  const empty = job.status === "done" && incremental && !job.messages ? "<p>没有新增或更新消息，导出基准已保存。</p>" : "";
  return `<div class="shop-export-status" role="status"><strong>${esc(mode)} · ${esc(labels[job.status] || job.status)}</strong><span>${job.scope === "local" ? "仅本地记录" : "平台历史 + 本地记录"}${esc(baseline)} · ${esc(phase)} · ${job.completed}/${job.conversations} 个会话 · ${job.messages} 条消息${esc(changes)}</span>${empty}${job.error ? `<p class="inline-error">${esc(job.error)}</p>` : ""}<div class="flex">${job.status === "done" ? `<a class="export-download" href="/api/shops/${shop.id}/exports/${job.id}/download" download>${incremental ? "下载本次增量 ZIP" : "下载全量 ZIP"}</a><a class="export-download" href="/api/shops/${shop.id}/exports/${job.id}/download?format=markdown" download>下载 Markdown</a>` : job.status === "running" ? `<button class="quiet small" data-action="cancel-export" data-id="${shop.id}" data-export="${job.id}">暂停导出</button>` : `<button class="small" data-action="resume-export" data-id="${shop.id}" data-export="${job.id}">继续导出</button>`}</div></div>`;
}
function openShopExport(id) {
  const shop = state.shops.find(item => item.id === Number(id));
  if (!shop) return;
  modal("导出聊天记录", `<form id="export-shop-form"><p><strong>${esc(shop.name)}</strong></p><fieldset><legend>导出方式</legend><label class="check"><input type="radio" name="mode" value="incremental" checked>增量：只导出上次成功后新增或更新的消息</label><label class="check"><input type="radio" name="mode" value="full">全量：重新导出全部可获取记录</label></fieldset><fieldset><legend>数据范围</legend><label class="check"><input type="radio" name="scope" value="platform" ${shop.platform_shop_id ? "checked" : ""}>平台可获取的历史 + 本地记录</label><label class="check"><input type="radio" name="scope" value="local" ${shop.platform_shop_id ? "" : "checked"}>仅本地保存的记录</label></fieldset><p id="export-baseline" class="filemeta"></p><div class="notice">每个店铺、每种数据范围单独记录导出基准。第一次增量会导出全部可获取记录；后续包括老会话的新消息、延迟收到的消息和撤回等更新。失败或暂停不会推进基准。<br>增量文件仍需扫描当前可获取的历史，耗时可能与全量接近。ZIP 含 Markdown、CSV、JSON 和 HTML，也可单独下载 .md；图片、视频保留在线地址。平台范围需要有效登录态，受平台权限和历史保留范围限制。导出不会调用 AI 或发送买家消息。</div><div class="form-actions"><button type="button" data-action="close-modal">取消</button><button class="primary" type="submit">开始导出</button></div></form>`);
  const form = $("#export-shop-form");
  const refreshBaseline = () => {
    const scope = new FormData(form).get("scope");
    const baseline = shop.export_baselines?.[scope];
    $("#export-baseline").textContent = baseline ? `此范围上次成功导出：${when(baseline.at)}` : "此范围尚无成功导出，将从全量建立基准。";
  };
  form.addEventListener("change", refreshBaseline);
  refreshBaseline();
  form.addEventListener("submit", event => {
    event.preventDefault();
    busy($("button[type=submit]",event.target), async () => {
      const data = new FormData(form);
      await post(`/api/shops/${id}/exports`, {scope:data.get("scope"), mode:data.get("mode")});
      closeModal();
      await renderShops();
      toast("导出已开始，可在店铺卡片查看进度。");
    });
  });
}
function openDeleteShop(id) {
  const shop = state.shops.find(item => item.id === Number(id));
  if (!shop) return;
  modal("删除店铺", `<form id="delete-shop-form"><p>删除 <strong>${esc(shop.name)}</strong>？</p><div class="notice">将停止消息连接、关闭登录窗口并清除保存的店铺登录态。聊天记录和知识库会保留在“已删除店铺”，可以恢复；恢复后需重新登录拼多多。不会删除拼多多平台上的店铺或消息。</div><div class="field"><label for="delete-shop-name">输入完整店铺名称确认</label><input id="delete-shop-name" name="confirm_name" required autocomplete="off" placeholder="${esc(shop.name)}"></div><div class="form-actions"><button type="button" data-action="close-modal">取消</button><button class="danger" type="submit">确认删除店铺</button></div></form>`);
  $("#delete-shop-form").addEventListener("submit", event => {
    event.preventDefault();
    busy($("button[type=submit]",event.target), async () => {
      await api(`/api/shops/${id}`, {method:"DELETE",body:JSON.stringify({confirm_name:new FormData(event.target).get("confirm_name")})});
      closeModal();
      state.inboxEpoch = (state.inboxEpoch || 0) + 1;
      state.conversationId = null; state.localTranscript = null; state.historyItems = []; state.documents = [];
      await loadShops(); shell(); await renderShops();
      toast("店铺已移入已删除列表，资料和对话已保留。");
    });
  });
}
async function openDeletedShops() {
  const deleted = await api("/api/shops/deleted");
  modal("已删除店铺", deleted.length ? `<div class="deleted-shops">${deleted.map(shop => `<div class="deleted-shop"><div><strong>${esc(shop.name)}</strong><p class="filemeta">${shop.platform_shop_id ? "店铺 ID：" + esc(shop.platform_shop_id) + " · " : ""}删除于 ${when(shop.deleted_at)}</p></div><button class="small" data-action="restore-shop" data-id="${shop.id}">恢复店铺</button></div>`).join("")}</div><div class="notice">恢复后聊天和知识库可继续使用，需重新登录拼多多才能连接消息。</div>` : empty("暂无已删除店铺","删除的店铺可在此恢复。"), true);
}
async function renderShops() {
  await loadShops();
  $("#page").innerHTML =
    heading(
      "店铺连接",
      "在独立浏览器完成登录，系统自动识别店铺。",
      '<div class="flex"><button data-action="deleted-shops">已删除店铺</button><button class="primary" data-action="new-shop">' +
        icon("plus") +
        " 新建店铺</button></div>",
    ) +
    `<div class="grid3">${state.shops
      .map((shop) => {
        const login = shop.login || { status: "idle", detail: "" };
        const active = ["starting", "waiting", "verifying"].includes(
          login.status,
        );
        const label = {
          starting: "正在打开浏览器",
          waiting: "等待你完成登录",
          verifying: "正在核对店铺",
          done: "店铺已绑定",
          failed: "登录未完成",
          cancelled: "登录已取消",
        }[login.status];
        return `<section class="panel shop-card"><div class="flex between"><span class="avatar">${icon("shop")}</span>${badge(shop.connection.status)}</div>
        <div class="gap"></div><h3>${esc(shop.name)}</h3><div class="shop-id">${shop.platform_shop_id ? "拼多多店铺 ID：" + esc(shop.platform_shop_id) : "等待绑定拼多多商家账号"}</div>
        <div class="field"><label for="mode-${shop.id}">接待方式</label><select id="mode-${shop.id}" data-mode-shop="${shop.id}">
          <option value="manual" ${shop.mode === "manual" ? "selected" : ""}>人工接待</option><option value="review" ${shop.mode === "review" ? "selected" : ""}>AI 草稿，人工审核发送</option><option value="auto" ${shop.mode === "auto" ? "selected" : ""}>有依据时自动回复</option><option value="ai_takeover" ${shop.mode === "ai_takeover" ? "selected" : ""}>AI 接管</option></select><small>${shop.mode === "ai_takeover" ? "新文字咨询直接由模型处理并回复，不受本地关键词开关限制；人工接管优先。缺少依据的商品事实、图片等消息转人工，历史同步不触发回复。" : "AI 接管由模型直接回复新文字咨询。AI 草稿需人工发送；自动回复仅发送有资料依据的回答。"}</small></div>
        <div class="shop-state">${esc(shop.connection.detail)}</div>
        ${shop.connection.build || shop.protocol?.build ? `<div class="notice"><strong>协议状态</strong><br>JSON 通道 · 构建 ${esc(shop.connection.build || shop.protocol.build)}<br>${shop.connection.sync === "ok" ? "消息序号同步正常" : shop.protocol?.sync_endpoint === "ok" ? "鉴权、心跳、同步接口检查通过" : "等待消息同步检查"}<br><span class="muted">真实消息发送尚未验证</span></div><div class="gap"></div>` : ""}
        ${label ? `<div class="notice ${login.status === "failed" ? "orange" : ""}"><strong>${label}</strong><br>${esc(login.detail)}</div><div class="gap"></div>` : ""}
        <div class="shop-actions"><button class="primary small" data-action="login-shop" data-id="${shop.id}">${active ? "显示登录浏览器" : "浏览器登录"}</button>
          ${active ? `<button class="small" data-action="cancel-login" data-id="${shop.id}">取消登录</button><button class="quiet small" data-action="restart-login" data-id="${shop.id}">重新打开</button>` : ""}
          <button class="small" data-action="${shop.connection.status === "connected" ? "disconnect-shop" : "connect-shop"}" data-id="${shop.id}">${shop.connection.status === "connected" ? "断开连接" : "连接消息"}</button>
          <button class="quiet small" data-action="check-protocol" data-id="${shop.id}">检查协议</button>
          <button class="quiet small" data-action="bind-shop" data-id="${shop.id}">导入登录态</button></div><div class="shop-maintenance"><button class="small" data-action="export-shop" data-id="${shop.id}" ${shop.export?.status === "running" ? "disabled" : ""}>导出聊天记录</button><button class="quiet small danger" data-action="delete-shop" data-id="${shop.id}" ${shop.export?.status === "running" ? "disabled" : ""}>删除店铺</button></div>${exportStatusHTML(shop)}</section>`;
      })
      .join(
        "",
      )}</div><div class="gap"></div><div class="notice">登录浏览器会在运行客服服务的电脑上打开。请直接在真实网页中输入、扫码并完成验证；识别并保存登录状态后，登录窗口会自动关闭。工作台会自动更新店铺名称。</div>`;
  document.querySelectorAll("[data-mode-shop]").forEach((select) =>
    select.addEventListener("change", () =>
      busy(select, async () => {
        try {
          await post(`/api/shops/${select.dataset.modeShop}/mode`, {mode:select.value});
        } catch(error) {
          await renderShops();
          throw error;
        }
        await renderShops();
        toast("接待方式已更新。");
      }),
    ),
  );
}

async function renderModel() {
  const config = await api("/api/model");
  $("#page").innerHTML =
    heading(
      "连接你的大模型",
      "使用你已有的 API。商品图片识别可单独指定看图模型。",
    ) +
    `<div class="grid2"><section class="panel"><div class="panel-head"><h2>模型连接设置</h2><span class="badge blue">Chat Completions 兼容接口</span></div><form id="model-form" class="panel-body"><div class="field"><label for="model-base">API 接口地址</label><input id="model-base" name="base_url" type="url" value="${esc(config.base_url)}" required placeholder="https://你的接口地址/v1"><small>填写服务商给出的 Base URL，也支持完整的 /chat/completions 地址。</small></div><div class="field"><label for="model-name">文字模型名称</label><input id="model-name" name="model" value="${esc(config.model)}" required placeholder="填写服务商提供的模型标识"></div><div class="field"><label for="model-key">API Key</label><input id="model-key" name="api_key" type="password" autocomplete="new-password" placeholder="${config.has_key ? "密钥已保存，留空保留原密钥" : "填写你的 API Key"}"><small>密钥加密保存在服务器，不会返回给浏览器。</small></div><div class="field"><label for="model-system-prompt">AI 系统提示词（全局）</label><textarea id="model-system-prompt" name="system_prompt" rows="9" maxlength="12000" placeholder="例如：你是店铺的专业客服。使用简洁、自然的中文，每次先回答客户最关心的问题；施工咨询先确认墙面条件和面积。">${esc(config.system_prompt || "")}</textarea><small>配置客服角色、回复语气和接待流程，最多 12000 字。保存后对全部店铺的新回复及问答测试生效；留空使用默认要求。商品事实以已审核知识为准，工作台会保留结构化回复要求。此项不用于图片转录或资料学习。</small></div><label class="check"><input id="vision-enabled" name="vision_enabled" type="checkbox" ${config.vision_enabled ? "checked" : ""}>启用图片和扫描件识别</label><div class="field"><label for="vision-model">看图模型名称（选填）</label><input id="vision-model" name="vision_model" value="${esc(config.vision_model)}" placeholder="留空使用文字模型，需支持图片输入"><small>图片会通过相同的接口地址和 API Key 发送给该模型。</small></div><div class="notice"><label class="check"><input id="use-local-keywords" name="use_local_keywords" type="checkbox" ${config.use_local_keywords !== false ? "checked" : ""}>使用本地关键词检索</label><small>开启：本地检索未命中时直接提示人工，可能跳过模型。<br>关闭：不做本地关键词筛选，将当前商品的已入库资料和前文交给模型理解；资料较多时受上下文预算限制，API 用量可能增加。<br>两种模式都不允许编造商品事实；适用于问答测试和普通接待。店铺选择“AI 接管”时会绕过本地关键词筛选。</small></div><div class="gap"></div><label class="check"><input name="allow_private" type="checkbox" ${config.allow_private ? "checked" : ""}>允许本机 / 局域网模型端点</label><div class="form-actions"><button type="button" data-action="test-model">测试已保存的连接</button><button class="primary" type="submit">保存配置${icon("check")}</button></div><div id="model-result" class="connection-status"></div></form></section><aside class="model-help"><h3>两种模型，各有所长</h3><p><strong>文字模型</strong><br>根据检索到的商品资料和上下文，生成客服回复。</p><p><strong>看图模型</strong><br>读取商品长图、参数截图和扫描 PDF，提取可编辑的文字资料。你的接口需支持 image_url 图片输入。</p><p><strong>资料如何使用</strong><br>只有你确认入库的内容才参与检索。回答时会附上引用片段，资料不足则进入人工处理。</p><p><strong>费用与数据</strong><br>模型调用使用你的 API 额度，必要的商品资料和客户问题会发送到你配置的接口。</p></aside></div>`;
  $("#model-form").addEventListener("submit", (event) => {
    event.preventDefault();
    busy($("button[type=submit]", event.target), async () => {
      const form = new FormData(event.target);
      await post("/api/model", {
        base_url: form.get("base_url"),
        model: form.get("model"),
        system_prompt: form.get("system_prompt"),
        api_key: form.get("api_key"),
        vision_model: form.get("vision_model"),
        vision_enabled: form.has("vision_enabled"),
        allow_private: form.has("allow_private"),
        use_local_keywords: form.has("use_local_keywords"),
      });
      $("#model-key").value = "";
      $("#model-key").placeholder = "密钥已保存，留空保留原密钥";
      toast("模型配置已保存。");
    });
  });
}
async function renderTeam() {
  const members = await api("/api/team");
  $("#page").innerHTML =
    heading(
      "团队成员",
      "一个商家团队，共享店铺资料并协作处理客户会话。",
      '<button class="primary" data-action="new-member">' +
        icon("plus") +
        " 添加成员</button>",
    ) +
    `<section class="panel"><div class="panel-head"><h2>成员与权限</h2><span class="muted smalltext">${members.filter((item) => item.active).length} 位已启用成员</span></div><div class="table-wrap"><table><thead><tr><th>成员</th><th>角色</th><th>状态</th><th>加入时间</th><th>操作</th></tr></thead><tbody>${members.map((item) => `<tr><td><div class="flex"><span class="avatar">${esc(item.username.slice(0, 1))}</span>${esc(item.username)}${item.id === state.user.id ? '<span class="badge">你</span>' : ""}</div></td><td>${item.role === "admin" ? "管理员" : "客服成员"}</td><td><span class="badge ${item.active ? "green" : ""}">${item.active ? "已启用" : "已停用"}</span></td><td class="muted">${when(item.created_at)}</td><td>${item.id !== state.user.id && item.active ? `<button class="quiet small danger" data-action="disable-member" data-id="${item.id}">停用账号</button>` : "—"}</td></tr>`).join("")}</tbody></table></div></section><div class="gap"></div><div class="grid2"><div class="notice"><strong>管理员</strong><br>配置大模型、连接店铺、维护知识库、管理成员，以及接待客户。</div><div class="notice"><strong>客服成员</strong><br>查看知识来源、测试问答、接管会话、审核 AI 草稿并发送回复。团队成员共享本商家的全部店铺。</div></div>`;
}
function timelineMessage(message, buyerName = "") {
  return renderMessage(message, buyerName, when);
}
function finishMessageLayout(previous = null) {
  const box = $("#thread-messages");
  if (!box) return;
  const follow = !previous || previous.follow;
  box.dataset.followBottom = String(follow);
  box.scrollTop = follow ? box.scrollHeight : previous.top;
  box.addEventListener("scroll", () => {
    box.dataset.followBottom = String(box.scrollHeight - box.scrollTop - box.clientHeight < 90);
  }, { passive: true });
}
function threadScrollPosition() {
  const box = $("#thread-messages");
  return box ? { top: box.scrollTop, follow: box.scrollHeight - box.scrollTop - box.clientHeight < 90 } : null;
}
function previewImage(url) {
  url = safeURL(url, true);
  if (!url) return;
  modal("图片预览", `<div class="image-preview chat-media is-loading"><img src="${esc(url)}" alt="聊天图片原图" referrerpolicy="no-referrer"><span class="media-loading">加载原图…</span><span class="media-fallback">图片加载失败，可以打开原图查看。</span></div><a href="${esc(url)}" target="_blank" rel="noopener noreferrer">打开原图 ↗</a>`, true);
}
document.addEventListener("load", (event) => {
  const image = event.target;
  if (image.tagName !== "IMG") return;
  const container = image.closest(".chat-media");
  if (!container) return;
  container.classList.remove("is-loading", "is-error");
  const box = image.closest(".chat-messages");
  if (box?.dataset.followBottom === "true") box.scrollTop = box.scrollHeight;
}, true);
document.addEventListener("error", (event) => {
  const media = event.target;
  if (!["IMG", "VIDEO"].includes(media.tagName)) return;
  const container = media.closest(".chat-media");
  if (container) {
    container.classList.remove("is-loading");
    container.classList.add("is-error");
  }
}, true);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && state.modal) closeModal();
});
function mergeTranscript(first, second, key = "id") {
  const map = new Map(
    [...first, ...second].map((item) => [String(item[key]), item]),
  );
  for (const message of map.values()) {
    const target = map.get(String(message.presentation?.withdraw_target));
    if (target && target !== message) {
      target.presentation = { version: 1, kind: "withdrawn", body: message.presentation.body };
      message.presentation.hidden = true;
    }
  }
  return [...map.values()].sort(
    (a, b) =>
      a.created_at - b.created_at ||
      String(a[key]).localeCompare(String(b[key]), undefined, {
        numeric: true,
      }),
  );
}
async function renderInbox() {
  state.inboxEpoch = (state.inboxEpoch || 0) + 1;
  state.historyLoading = false;
  state.inboxSource = state.inboxSource || "live";
  $("#page").innerHTML =
    heading("接待工作台", "实时接待与历史记录，按店铺分别查看。") +
    `<div class="inbox-tabs" role="tablist" aria-label="会话来源"><button role="tab" data-inbox-source="live" aria-selected="${state.inboxSource === "live"}">实时接待</button><button role="tab" data-inbox-source="history" aria-selected="${state.inboxSource === "history"}">历史会话</button></div>
     <section class="panel inbox"><div class="conversation-list"><div id="conversation-toolbar" class="conversation-toolbar"></div><div id="conversations"></div><div id="conversation-pagination" class="history-pagination"></div></div><div id="thread" class="thread">${empty("选择一个客户会话", state.inboxSource === "history" ? "历史浏览不会触发自动回复。" : "连接店铺后，新的买家咨询会显示在这里。")}</div></section>`;
  state.conversationSignature = "";
  if (state.inboxSource === "history") {
    state.historyPage = 0;
    state.historyItems = [];
    state.historySelected = null;
    state.historyTranscript = null;
    $("#conversation-toolbar").innerHTML =
      `<div class="flex between"><span>平台历史会话</span><button class="quiet small" data-action="refresh-history">刷新</button></div><input id="history-filter" placeholder="筛选已加载的买家或内容" aria-label="筛选已加载的历史会话"><div id="history-list-count" class="filemeta">正在读取平台记录…</div>`;
    $("#history-filter").addEventListener("input", drawHistoryConversations);
    await loadHistoryConversations();
  } else {
    $("#conversation-toolbar").innerHTML =
      '最近会话 <span class="muted">· 每 5 秒更新</span>';
    await refreshInbox();
  }
}
async function refreshInbox() {
  if (state.inboxSource === "history") return;
  const shopId = state.shopId;
  const conversations = await api("/api/conversations?shop_id=" + shopId);
  if (
    state.page !== "inbox" ||
    state.inboxSource === "history" ||
    shopId !== state.shopId ||
    !$("#conversations")
  )
    return;
  $("#conversations").innerHTML = conversations.length
    ? conversations
        .map(
          (item) =>
            `<button class="conversation-item ${item.id === state.conversationId ? "active" : ""}" data-conversation="${item.id}"><div class="flex between"><span class="name">${esc(buyerLabel(item))}</span>${item.needs_human ? '<span class="badge orange">待处理</span>' : item.mode === "human" ? '<span class="badge blue">人工中</span>' : selectedShop()?.mode === "ai_takeover" ? '<span class="badge green">AI 接管</span>' : ""}</div><div class="buyer-id">ID：${esc(item.buyer_uid)}</div><div class="preview">${esc(item.preview || "暂无消息")}</div><div class="filemeta">${when(item.updated_at)}${item.assignee_name ? " · " + esc(item.assignee_name) : ""}</div></button>`,
        )
        .join("")
    : empty("暂无会话", "可以切换到历史会话，查看之前的咨询。");
  if (state.conversationId) await refreshThread();
}
async function refreshThread() {
  const id = state.conversationId;
  const value = await api("/api/conversations/" + id);
  if (
    state.page !== "inbox" ||
    state.inboxSource === "history" ||
    id !== state.conversationId ||
    !$("#thread")
  )
    return;
  // A successful polling timestamp alone must not rebuild the reply form and steal input focus.
  const signature = JSON.stringify({...value, platform_history: {...value.platform_history, checked_at: 0}});
  if (state.conversationSignature === signature) return;
  state.conversationSignature = signature;
  if (state.localTranscript?.id === id) {
    state.localTranscript.messages = mergeTranscript(
      state.localTranscript.messages,
      value.messages,
    );
    state.localTranscript.total = value.total;
    state.localTranscript.platformHistory = value.platform_history;
    if (!state.localTranscript.loadedEarlier) {
      state.localTranscript.hasMore = value.has_more;
      state.localTranscript.cursor = value.next_before_id;
    }
  } else
    state.localTranscript = {
      id,
      messages: value.messages,
      total: value.total,
      hasMore: value.has_more,
      cursor: value.next_before_id,
      loadedEarlier: false,
      platformHistory: value.platform_history,
    };
  state.activeConversation = value.conversation;
  drawLocalThread();
}
function drawLocalThread() {
  const conversation = state.activeConversation,
    local = state.localTranscript;
  if (
    !conversation ||
    !local ||
    !$("#thread") ||
    state.inboxSource === "history"
  )
    return;
  const id = conversation.id,
    owned = conversation.assignee === state.user.id;
  const remote = local.platformHistory || {status:"pending",complete:false,has_more:true};
  const olderAvailable = local.hasMore || remote.has_more;
  const boundary = local.hasMore ? "加载更早记录" : remote.has_more ? "加载更早平台记录" : "";
  const syncNotice = remote.status === "error" || remote.status === "unavailable"
    ? `<div class="transcript-sync-error" role="status">${esc(remote.error || "平台记录尚未同步完整")}${remote.status === "error" ? ' <button class="quiet small" data-action="sync-platform">重试同步</button>' : ""}</div>`
    : remote.pending_gap ? '<div class="transcript-sync-status" role="status">正在补齐断线期间的消息，可继续查看已有记录。</div>' : "";
  const saved =
    Number($("#thread").dataset.id) === id
      ? $("#reply-content")?.value || ""
      : "";
  const previousScroll = Number($("#thread").dataset.id) === id ? threadScrollPosition() : null;
  state.activeMessages = local.messages;
  $("#thread").dataset.id = id;
  $("#thread").innerHTML =
    `<div class="thread-head flex between"><div><h3>${esc(buyerLabel(conversation))}</h3><p class="muted smalltext">用户 ID：${esc(conversation.buyer_uid)} · ${conversation.goods_id ? "商品 " + esc(conversation.goods_id) : "尚未识别具体商品"} · ${conversation.mode === "human" ? "人工接待中" : selectedShop()?.mode === "ai_takeover" ? "AI 接管中" : "AI 辅助接待"} · 已显示 ${local.messages.length} / 本地 ${local.total} 条${remote.complete ? " · 平台历史已加载到底" : remote.has_more ? " · 还有更早平台记录" : ""}</p></div><div class="flex"><button class="quiet small" data-action="sync-platform">同步平台记录</button><button class="small" data-action="buyer-orders">查看订单</button>${conversation.mode === "human" && owned ? `<button class="small" data-action="resume" data-id="${id}">交还 AI</button>` : `<button class="primary small" data-action="takeover" data-id="${id}">${conversation.assignee && !owned ? "由其他客服接待" : "接管会话"}</button>`}</div></div>
    ${syncNotice}
    ${conversationProduct(local.messages, conversation.goods_id)}
    <div class="chat-messages" id="thread-messages">${olderAvailable ? `<div class="history-load-earlier"><button class="small" data-action="older-local">${boundary}</button></div>` : `<div class="history-boundary">${remote.complete ? "已加载至平台最早可获取记录" : "以上为本地保存的最早记录，平台完整性尚未确认"}</div>`}${local.messages.map((message) => timelineMessage(message, buyerLabel(conversation))).join("")}</div>
    <form id="reply-form" class="chat-compose"><textarea id="reply-content" aria-label="客服回复" placeholder="${owned ? "输入回复，或使用 AI 草稿后编辑" : "请先接管会话，再审核并发送回复"}" maxlength="3000" ${owned ? "" : "disabled"}>${esc(saved)}</textarea><div class="compose-footer"><span>${owned ? "发送后以平台确认结果为准。" : "接管后会暂停该会话的 AI 自动回复。"}</span><button type="submit" class="primary" ${owned ? "" : "disabled"}>发送回复${icon("arrow")}</button></div></form>`;
  finishMessageLayout(previousScroll);
  $("#reply-form").addEventListener("submit", (event) => {
    event.preventDefault();
    busy($("button[type=submit]", event.target), async () => {
      const content = $("#reply-content").value.trim();
      if (!content) return;
      await post(`/api/conversations/${id}/send`, {
        content,
        request_key: requestId(),
      });
      $("#reply-content").value = "";
      await refreshInbox();
      toast("平台已确认发送。");
    });
  });
}
async function syncLiveTranscript() {
  const id=state.conversationId;
  if(!id)return;
  const result=await post(`/api/conversations/${id}/history`,{older:false});
  if(state.conversationId!==id || state.inboxSource==="history")return;
  state.conversationSignature="";
  await refreshThread();
  if(result.status==="error")toast(result.error,true);
}
async function loadOlderLocal() {
  const local = state.localTranscript, id = state.conversationId;
  if (!local || !local.messages.length) return;
  const box = $("#thread-messages"), height = box.scrollHeight, position = box.scrollTop;
  if (!local.hasMore && local.platformHistory?.has_more) {
    const result=await post(`/api/conversations/${id}/history`,{older:true});
    if(state.conversationId!==id || state.inboxSource==="history")return;
    local.platformHistory=result;
    if(result.status==="error") {
      drawLocalThread();
      toast(result.error,true);
      return;
    }
  } else if (!local.hasMore) return;
  const anchor=local.messages[0].id;
  const value = await api(`/api/conversations/${id}?before_id=${anchor}&limit=100`);
  if (state.conversationId !== id || state.inboxSource === "history") return;
  local.messages = mergeTranscript(local.messages, value.messages);
  local.hasMore = value.has_more;
  local.cursor = value.next_before_id;
  local.total = value.total;
  local.platformHistory=value.platform_history;
  local.loadedEarlier = true;
  drawLocalThread();
  $("#thread-messages").scrollTop = $("#thread-messages").scrollHeight - height + position;
  $("#thread-messages").dataset.followBottom = "false";
}
async function loadHistoryConversations() {
  if (state.historyLoading) return;
  const shopId = state.shopId,
    page = (state.historyPage || 0) + 1,
    epoch = state.inboxEpoch;
  state.historyLoading = true;
  try {
    const value = await api(
      `/api/history/conversations?shop_id=${shopId}&page=${page}&size=20`,
    );
    if (
      state.shopId !== shopId ||
      state.page !== "inbox" ||
      state.inboxEpoch !== epoch ||
      state.inboxSource !== "history"
    )
      return;
    const unique = new Map(
      [...(state.historyItems || []), ...value.items].map((item) => [
        item.buyer_uid + ":" + item.chat_type,
        item,
      ]),
    );
    state.historyItems = [...unique.values()];
    state.historyPage = page;
    state.historyHasMore = value.has_more;
    state.historyTotal = value.total;
    drawHistoryConversations();
  } finally {
    if (state.inboxEpoch === epoch) state.historyLoading = false;
  }
}
function drawHistoryConversations() {
  if (state.inboxSource !== "history" || !$("#conversations")) return;
  const query = $("#history-filter")?.value.toLowerCase() || "";
  const items = (state.historyItems || [])
    .map((item, index) => ({ item, index }))
    .filter(({ item }) =>
      `${item.buyer_name} ${item.buyer_uid} ${item.preview}`
        .toLowerCase()
        .includes(query),
    );
  $("#conversations").innerHTML = items.length
    ? items
        .map(
          ({ item, index }) =>
            `<button class="conversation-item ${state.historySelected?.buyer_uid === item.buyer_uid ? "active" : ""}" data-history-index="${index}"><div class="flex between"><span class="name">${esc(buyerLabel(item))}</span><span class="badge">历史</span></div><div class="buyer-id">ID：${esc(item.buyer_uid)}</div><div class="preview">${esc(item.preview)}</div><div class="filemeta">${item.updated_at ? when(item.updated_at) : "时间未知"}</div></button>`,
        )
        .join("")
    : empty(
        "没有历史会话",
        query ? "当前筛选未匹配已加载的记录。" : "平台暂未返回会话。",
      );
  $("#history-list-count").textContent =
    `已加载 ${state.historyItems.length} 个会话${state.historyTotal !== null ? " / 平台共 " + state.historyTotal + " 个" : " · 平台未提供总数"}`;
  $("#conversation-pagination").innerHTML = state.historyHasMore
    ? '<button class="small" data-action="more-history">加载更多会话</button>'
    : '<span class="muted smalltext">已加载到列表末尾</span>';
}
async function selectHistory(index) {
  const item = state.historyItems[index];
  if (!item) return;
  state.historySelected = item;
  state.historyTranscript = {
    messages: [],
    hasMore: true,
    cursor: "",
    loading: false,
  };
  drawHistoryConversations();
  $("#thread").innerHTML =
    '<div class="empty"><span class="spinner"></span><p>正在读取历史聊天记录…</p></div>';
  await loadHistoryMessages();
}
async function loadHistoryMessages() {
  const item = state.historySelected,
    transcript = state.historyTranscript,
    shopId = state.shopId;
  if (!item || !transcript || transcript.loading || !transcript.hasMore) return;
  const box = $("#thread-messages");
  const height = box?.scrollHeight || 0,
    position = box?.scrollTop || 0;
  const older = transcript.messages.length > 0;
  const params = new URLSearchParams({
    shop_id: shopId,
    buyer_uid: item.buyer_uid,
    size: 50,
    chat_type: item.chat_type || "",
  });
  if (transcript.cursor) params.set("before", transcript.cursor);
  transcript.loading = true;
  try {
    const value = await api("/api/history/messages?" + params);
    if (
      state.shopId !== shopId ||
      state.historySelected !== item ||
      state.inboxSource !== "history"
    )
      return;
    transcript.messages = mergeTranscript(
      transcript.messages,
      value.messages,
      "platform_id",
    );
    transcript.hasMore = value.has_more;
    transcript.cursor = value.next_cursor;
    drawHistoryThread();
    if (older)
      $("#thread-messages").scrollTop =
        $("#thread-messages").scrollHeight - height + position;
    else $("#thread-messages").scrollTop = $("#thread-messages").scrollHeight;
    finishMessageLayout(older ? {top: $("#thread-messages").scrollTop, follow: false} : null);
  } catch (error) {
    if (state.historySelected === item && !older)
      $("#thread").innerHTML = empty(
        "历史记录读取失败",
        error.message,
        '<button data-action="retry-history-messages">重试</button>',
      );
    throw error;
  } finally {
    transcript.loading = false;
  }
}
function drawHistoryThread() {
  const item = state.historySelected,
    transcript = state.historyTranscript;
  if (!item || !transcript || state.inboxSource !== "history") return;
  $("#thread").removeAttribute("data-id");
  $("#thread").innerHTML =
    `<div class="thread-head flex between"><div><h3>${esc(buyerLabel(item))}</h3><p class="muted smalltext">用户 ID：${esc(item.buyer_uid)} · ${transcript.hasMore ? "已加载" : "共"} ${transcript.messages.length} 条历史消息${transcript.hasMore ? " · 还有更早记录" : ""}</p></div><div class="flex"><button class="small" data-action="buyer-orders">查看订单</button><span class="badge blue">仅浏览</span><button class="small" data-action="history-takeover">接管并回复</button></div></div>
    ${conversationProduct(transcript.messages)}
    <div class="chat-messages" id="thread-messages">${transcript.hasMore ? '<div class="history-load-earlier"><button class="small" data-action="older-history">加载更早消息</button></div>' : '<div class="history-boundary">已到该会话最早消息</div>'}${transcript.messages.map((message) => timelineMessage({ ...message, state: "historical" }, buyerLabel(item))).join("") || empty("暂无聊天记录", "平台未返回这个会话的消息。")}</div>
    <div class="history-readonly">当前只浏览历史，不会调用 AI、发送消息或修改会话的接待状态。</div>`;
}
async function takeOverHistory() {
  const item = state.historySelected,
    shopId = state.shopId;
  if (!item) return;
  const result = await post("/api/history/open", {
    shop_id: shopId,
    buyer_uid: item.buyer_uid,
    buyer_name: item.buyer_name,
    chat_type: item.chat_type || "",
  });
  if (shopId !== state.shopId) return;
  state.inboxSource = "live";
  state.conversationId = result.conversation_id;
  state.localTranscript = null;
  await renderInbox();
  toast("已接管历史会话，AI 自动回复已暂停。可编辑后手动发送。");
}
async function loginShop(id) {
  await post(`/api/shops/${id}/login`);
  toast("正在打开独立浏览器，请在弹出的 Chrome / Edge 窗口完成登录。");
  await renderShops();
}

document.addEventListener("click", async (event) => {
  if (event.target.classList.contains("overlay") && state.modal) { closeModal(); return; }
  const source = event.target.closest("[data-inbox-source]");
  if (source) {
    state.inboxSource = source.dataset.inboxSource;
    await busy(source, renderInbox);
    return;
  }
  const historical = event.target.closest("[data-history-index]");
  if (historical) {
    await busy(historical, () =>
      selectHistory(Number(historical.dataset.historyIndex)),
    );
    return;
  }
  const nav = event.target.closest("[data-page]");
  if (nav && state.user) {
    await busy(nav, () => navigate(nav.dataset.page));
    return;
  }
  const filter = event.target.closest("[data-filter]");
  if (filter) {
    state.filter = filter.dataset.filter;
    document
      .querySelectorAll("[data-filter]")
      .forEach((button) =>
        button.classList.toggle("selected", button === filter),
      );
    drawDocuments();
    return;
  }
  const conversation = event.target.closest("[data-conversation]");
  if (conversation) {
    state.conversationId = Number(conversation.dataset.conversation);
    state.conversationSignature = "";
    await busy(conversation, refreshInbox);
    return;
  }
  const button = event.target.closest("[data-action]");
  if (!button) return;
  const id = button.dataset.id,
    action = button.dataset.action;
  await busy(button, async () => {
    switch (action) {
      case "buyer-orders": await showBuyerOrders(); break;
      case "learning-preview": await openLearningPreview(id); break;
      case "learning-start":
        await post(`/api/shops/${state.shopId}/products/${encodeURIComponent(id)}/learning`);
        closeModal(); await refreshProductCatalog(); toast("正在关联订单并整理商品咨询，完成后请审核。"); break;
      case "learning-evidence": await showLearningEvidence(button.dataset.job,Number(button.dataset.pageNumber)||1); break;
      case "learning-cancel": await post(`/api/shops/${state.shopId}/learning/${button.dataset.job}/cancel`); await refreshProductCatalog(); break;
      case "learning-resume": await post(`/api/shops/${state.shopId}/learning/${button.dataset.job}/resume`); await refreshProductCatalog(); break;
      case "sync-products": openProductSync(); break;
      case "confirm-product-sync":
        await post(`/api/shops/${state.shopId}/products/sync`);
        closeModal(); await refreshProductCatalog(); toast("正在同步商品并建立知识。"); break;
      case "view-product": await viewProduct(id); break;
      case "product-images": openProductImages(id); break;
      case "confirm-product-images":
        await post(`/api/shops/${state.shopId}/products/${encodeURIComponent(id)}/images`);
        closeModal(); await refreshProductCatalog(); toast("详情图识别已开始，完成后请审核入库。"); break;
      case "export-shop":
        openShopExport(id); break;
      case "delete-shop":
        openDeleteShop(id); break;
      case "deleted-shops":
        await openDeletedShops(); break;
      case "restore-shop":
        await post(`/api/shops/${id}/restore`);
        closeModal(); await loadShops(); shell(); await renderShops();
        toast("店铺已恢复，请重新登录以连接消息。"); break;
      case "cancel-export":
        await post(`/api/shops/${id}/exports/${button.dataset.export}/cancel`);
        await renderShops(); break;
      case "resume-export":
        await post(`/api/shops/${id}/exports/${button.dataset.export}/resume`);
        await renderShops(); break;
      case "preview-message-image":
        previewImage(button.dataset.imageUrl);
        break;
      case "more-history":
        await loadHistoryConversations();
        break;
      case "refresh-history":
        state.historyPage = 0;
        state.historyItems = [];
        await loadHistoryConversations();
        break;
      case "older-history":
      case "retry-history-messages":
        await loadHistoryMessages();
        break;
      case "history-takeover":
        await takeOverHistory();
        break;
      case "sync-platform":
        await syncLiveTranscript();
        break;
      case "older-local":
        await loadOlderLocal();
        break;
      case "close-modal":
        closeModal();
        break;
      case "logout":
        await post("/api/logout");
        state.user = null;
        state.history = [];
        state.testResults = [];
        state.conversationId = null;
        state.page = "home";
        await init();
        break;
      case "open-upload":
        openUpload();
        break;
      case "view-document":
        await viewDocument(id);
        break;
      case "retry-document":
        await post(`/api/documents/${id}/retry`);
        toast("已重新开始识别。");
        await renderKnowledge();
        break;
      case "delete-document":
        if (confirm("删除这份资料？删除后 AI 将无法再检索到它。")) {
          await api("/api/documents/" + id, { method: "DELETE" });
          closeModal();
          toast("资料已删除。");
          await renderKnowledge();
        }
        break;
      case "test-model": {
        const result = await post("/api/model/test");
        $("#model-result").textContent = result.detail;
        break;
      }
      case "new-shop":
        modal(
          "新建店铺",
          `<form id="shop-form"><div class="field"><label for="shop-name">店铺名称</label><input id="shop-name" name="name" required maxlength="100" placeholder="请输入店铺名称"></div><div class="form-actions"><button class="primary" type="submit">创建店铺</button></div></form>`,
        );
        $("#shop-form").addEventListener("submit", (event) => {
          event.preventDefault();
          busy($("button[type=submit]", event.target), async () => {
            await post(
              "/api/shops",
              Object.fromEntries(new FormData(event.target)),
            );
            closeModal();
            await loadShops();
            shell();
            await renderShops();
          });
        });
        break;
      case "bind-shop":
        modal(
          "导入店铺登录态",
          `<form id="bind-form"><div class="notice">适合已有浏览器登录态的用户。支持 Cookie 字典或浏览器导出的 Cookie 数组；系统会先向平台验证店铺身份。</div><div class="gap"></div><div class="field"><label for="cookie-json">Cookie JSON</label><textarea id="cookie-json" name="cookies" required autocomplete="off" placeholder="粘贴当前店铺的 Cookie JSON"></textarea><small>登录态加密保存，不会返回给其他客服。</small></div><div class="form-actions"><button class="primary" type="submit">验证并绑定</button></div></form>`,
        );
        $("#bind-form").addEventListener("submit", (event) => {
          event.preventDefault();
          busy($("button[type=submit]", event.target), async () => {
            await post(
              `/api/shops/${id}/bind`,
              Object.fromEntries(new FormData(event.target)),
            );
            closeModal();
            toast("已验证并绑定店铺。");
            await loadShops();
            shell();
            await renderShops();
          });
        });
        break;
      case "login-shop":
        await loginShop(Number(id));
        break;
      case "cancel-login":
        await post(`/api/shops/${id}/login-cancel`);
        await renderShops();
        toast("本次登录已取消。");
        break;
      case "restart-login":
        await post(`/api/shops/${id}/login-cancel`);
        await loginShop(Number(id));
        break;
      case "check-protocol": {
        const report = await post(`/api/shops/${id}/protocol-check`);
        toast(`协议检查通过：构建 ${report.build}，鉴权、心跳和同步接口正常。`);
        await renderShops();
        break;
      }
      case "connect-shop":
        await post(`/api/shops/${id}/connect`);
        toast("正在连接，请稍后查看连接状态。");
        await renderShops();
        break;
      case "disconnect-shop":
        await post(`/api/shops/${id}/disconnect`);
        toast("已断开消息连接。");
        await renderShops();
        break;
      case "new-member":
        modal(
          "添加团队成员",
          `<form id="member-form"><div class="field"><label for="member-name">用户名</label><input id="member-name" name="username" required maxlength="40" autocomplete="off"></div><div class="field"><label for="member-password">初始密码</label><input id="member-password" name="password" type="password" required minlength="10" maxlength="200" autocomplete="new-password"><small>至少 10 位。请将账号信息单独告知对应客服。</small></div><div class="field"><label for="member-role">成员角色</label><select id="member-role" name="role"><option value="agent">客服成员</option><option value="admin">管理员</option></select></div><div class="form-actions"><button type="submit" class="primary">创建成员</button></div></form>`,
        );
        $("#member-form").addEventListener("submit", (event) => {
          event.preventDefault();
          busy($("button[type=submit]", event.target), async () => {
            await post(
              "/api/team",
              Object.fromEntries(new FormData(event.target)),
            );
            closeModal();
            toast("团队成员已创建。");
            await renderTeam();
          });
        });
        break;
      case "disable-member":
        if (confirm("停用该成员账号？其当前登录会立即失效。")) {
          await post(`/api/team/${id}/disable`);
          toast("账号已停用。");
          await renderTeam();
        }
        break;
      case "takeover":
        await post(`/api/conversations/${id}/takeover`);
        await refreshInbox();
        toast("已接管，AI 将暂停自动回复该会话。");
        break;
      case "resume":
        await post(`/api/conversations/${id}/resume`);
        await refreshInbox();
        toast("后续新消息将交由 AI 处理。");
        break;
      case "use-draft": {
        const draft = state.activeMessages.find(
          (item) => item.id === Number(id),
        );
        if (!draft) return;
        if (state.activeConversation.assignee !== state.user.id) {
          await post(`/api/conversations/${state.conversationId}/takeover`);
          await refreshInbox();
        }
        $("#reply-content").value = draft.content;
        $("#reply-content").focus();
        break;
      }
    }
  });
});
setInterval(async () => {
  if (!state.user || document.hidden || state.modal) return;
  try {
    if (state.page === "inbox") await refreshInbox();
    else if (state.page === "knowledge") {
      await refreshProductCatalog();
      state.documents = await api("/api/documents?shop_id="+state.shopId);
      drawDocuments();
    } else if (state.page === "shops") {
      const previous = JSON.stringify(state.shops);
      await loadShops();
      if (previous !== JSON.stringify(state.shops)) await renderShops();
    }
  } catch {
    /* Visible actions report errors; polling retries on the next tick. */
  }
}, 5000);
init();

document.addEventListener("visibilitychange", () => {
  if (!document.hidden && state.user && state.page === "shops")
    renderShops().catch((error) => toast(error.message, true));
});
