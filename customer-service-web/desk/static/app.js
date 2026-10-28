import { buyerLabel, timelineMessage as renderMessage, conversationProduct, safeURL } from "./messages.js";
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
    `<div class="stats">${stats.map((item) => `<section class="stat"><div class="stat-label">${item[0]}${icon(item[3])}</div><div class="stat-number">${item[1]}</div><div class="stat-foot">${item[2]}</div></section>`).join("")}</div><section class="hero"><div><div class="eyebrow">KNOWLEDGE FIRST</div><h2>让 AI 先读懂你的商品。</h2><p>图片、文档、表格，都可以成为店铺的专属资料。确认知识后，先在问答测试中检查回答，再交给 AI 辅助接待。</p><div class="gap"></div><button class="primary" data-page="knowledge">管理商品资料${icon("arrow")}</button></div><div class="hero-mark">${icon("book")}</div></section><div class="grid2"><section class="panel"><div class="panel-head"><h2>开始接待前，完成这几步</h2><span class="badge blue">快速开始</span></div><div class="panel-body steps">${steps.map((item, index) => `<div class="step ${item[2] ? "done" : ""}"><span class="step-n">${item[2] ? "✓" : String(index + 1).padStart(2, "0")}</span><div class="step-content"><h3>${item[0]}</h3><p>${item[1]}</p></div>${admin() || !["model", "shops"].includes(item[3]) ? `<button class="quiet small" data-page="${item[3]}" aria-label="前往${item[0]}">${icon("arrow")}</button>` : ""}</div>`).join("")}</div></section><section class="panel"><div class="panel-head"><h2>当前店铺</h2>${badge(shop?.connection.status || "offline")}</div><div class="panel-body"><div class="flex"><span class="avatar">${icon("shop")}</span><div><h3>${esc(shop?.name)}</h3><p class="muted smalltext">${shop?.platform_shop_id ? "拼多多店铺 " + esc(shop.platform_shop_id) : "尚未绑定拼多多账号"}</p></div></div><div class="gap"></div><div class="notice">${esc(shop?.connection.detail || "尚未连接消息服务。")}<br>当前接待模式：${{ review: "AI 生成草稿，由客服审核发送", manual: "人工接待", auto: "有资料依据时自动回复" }[shop?.mode] || "AI 草稿"}</div><div class="gap"></div><h3>资料使用方式</h3><p class="muted smalltext">商品资料请填写对应的拼多多商品 ID。发货、退换货等通用政策可不填商品 ID。不同店铺的资料分别检索。</p><div class="gap"></div><button data-page="inbox">进入接待工作台${icon("arrow")}</button></div></section></div>`;
}
async function renderKnowledge() {
  state.documents = await api("/api/documents?shop_id=" + state.shopId);
  $("#page").innerHTML =
    heading(
      "店铺知识库",
      "把你已有的商品资料，整理成 AI 回答的依据。",
      admin()
        ? '<button class="primary" data-action="open-upload">' +
            icon("plus") +
            " 上传资料</button>"
        : "",
    ) +
    `<section class="panel"><div class="panel-head"><h2>资料库 <span class="muted smalltext">${state.documents.length} 份资料</span></h2><span class="badge blue">按当前店铺检索</span></div><div class="filters"><button data-filter="all" class="selected">全部资料</button><button data-filter="ready">已入库</button><button data-filter="draft">待审核</button><input id="document-search" aria-label="搜索资料" placeholder="搜索标题或商品 ID"></div><div id="document-list"></div></section><div class="gap"></div><div class="grid2"><div class="notice"><strong>商品资料</strong><br>适合规格、尺寸、材质、使用方法和注意事项。请关联商品 ID，避免不同商品的知识混用。</div><div class="notice"><strong>通用政策</strong><br>适合发货说明、退换货政策和店铺常见问答。作为本店所有商品的共同参考。</div></div>`;
  state.filter = "all";
  state.search = "";
  drawDocuments();
  $("#document-search").addEventListener("input", (event) => {
    state.search = event.target.value;
    drawDocuments();
  });
}
function drawDocuments() {
  if (!$("#document-list")) return;
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
        state.history.push(
          { role: "user", content: question },
          { role: "assistant", content: result.answer },
        );
        state.history = state.history.slice(-10);
        state.testResults.push(result);
        if ($("#test-sources"))
          $("#test-sources").innerHTML =
            `<div class="notice ${result.needs_human ? "orange" : ""}">${esc(result.reason || "回答已生成。")}</div>` +
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
async function renderShops() {
  await loadShops();
  $("#page").innerHTML =
    heading(
      "店铺连接",
      "在独立浏览器完成登录，系统自动识别店铺。",
      '<button class="primary" data-action="new-shop">' +
        icon("plus") +
        " 新建店铺</button>",
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
          <option value="manual" ${shop.mode === "manual" ? "selected" : ""}>人工接待</option><option value="review" ${shop.mode === "review" ? "selected" : ""}>AI 草稿，人工审核发送</option><option value="auto" ${shop.mode === "auto" ? "selected" : ""}>有依据时自动回复</option></select></div>
        <div class="shop-state">${esc(shop.connection.detail)}</div>
        ${shop.connection.build || shop.protocol?.build ? `<div class="notice"><strong>协议状态</strong><br>JSON 通道 · 构建 ${esc(shop.connection.build || shop.protocol.build)}<br>${shop.connection.sync === "ok" ? "消息序号同步正常" : shop.protocol?.sync_endpoint === "ok" ? "鉴权、心跳、同步接口检查通过" : "等待消息同步检查"}<br><span class="muted">真实消息发送尚未验证</span></div><div class="gap"></div>` : ""}
        ${label ? `<div class="notice ${login.status === "failed" ? "orange" : ""}"><strong>${label}</strong><br>${esc(login.detail)}</div><div class="gap"></div>` : ""}
        <div class="shop-actions"><button class="primary small" data-action="login-shop" data-id="${shop.id}">${active ? "显示登录浏览器" : "浏览器登录"}</button>
          ${active ? `<button class="small" data-action="cancel-login" data-id="${shop.id}">取消登录</button><button class="quiet small" data-action="restart-login" data-id="${shop.id}">重新打开</button>` : ""}
          <button class="small" data-action="${shop.connection.status === "connected" ? "disconnect-shop" : "connect-shop"}" data-id="${shop.id}">${shop.connection.status === "connected" ? "断开连接" : "连接消息"}</button>
          <button class="quiet small" data-action="check-protocol" data-id="${shop.id}">检查协议</button>
          <button class="quiet small" data-action="bind-shop" data-id="${shop.id}">导入登录态</button></div></section>`;
      })
      .join(
        "",
      )}</div><div class="gap"></div><div class="notice">登录浏览器会在运行客服服务的电脑上打开。请直接在真实网页中输入、扫码并完成验证；识别并保存登录状态后，登录窗口会自动关闭。工作台会自动更新店铺名称。</div>`;
  document.querySelectorAll("[data-mode-shop]").forEach((select) =>
    select.addEventListener("change", () =>
      busy(select, async () => {
        await post(`/api/shops/${select.dataset.modeShop}/mode`, {
          mode: select.value,
        });
        await loadShops();
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
    `<div class="grid2"><section class="panel"><div class="panel-head"><h2>模型连接设置</h2><span class="badge blue">Chat Completions 兼容接口</span></div><form id="model-form" class="panel-body"><div class="field"><label for="model-base">API 接口地址</label><input id="model-base" name="base_url" type="url" value="${esc(config.base_url)}" required placeholder="https://你的接口地址/v1"><small>填写服务商给出的 Base URL，也支持完整的 /chat/completions 地址。</small></div><div class="field"><label for="model-name">文字模型名称</label><input id="model-name" name="model" value="${esc(config.model)}" required placeholder="填写服务商提供的模型标识"></div><div class="field"><label for="model-key">API Key</label><input id="model-key" name="api_key" type="password" autocomplete="new-password" placeholder="${config.has_key ? "密钥已保存，留空保留原密钥" : "填写你的 API Key"}"><small>密钥加密保存在服务器，不会返回给浏览器。</small></div><label class="check"><input id="vision-enabled" name="vision_enabled" type="checkbox" ${config.vision_enabled ? "checked" : ""}>启用图片和扫描件识别</label><div class="field"><label for="vision-model">看图模型名称（选填）</label><input id="vision-model" name="vision_model" value="${esc(config.vision_model)}" placeholder="留空使用文字模型，需支持图片输入"><small>图片会通过相同的接口地址和 API Key 发送给该模型。</small></div><label class="check"><input name="allow_private" type="checkbox" ${config.allow_private ? "checked" : ""}>允许本机 / 局域网模型端点</label><div class="form-actions"><button type="button" data-action="test-model">测试已保存的连接</button><button class="primary" type="submit">保存配置${icon("check")}</button></div><div id="model-result" class="connection-status"></div></form></section><aside class="model-help"><h3>两种模型，各有所长</h3><p><strong>文字模型</strong><br>根据检索到的商品资料和上下文，生成客服回复。</p><p><strong>看图模型</strong><br>读取商品长图、参数截图和扫描 PDF，提取可编辑的文字资料。你的接口需支持 image_url 图片输入。</p><p><strong>资料如何使用</strong><br>只有你确认入库的内容才参与检索。回答时会附上引用片段，资料不足则进入人工处理。</p><p><strong>费用与数据</strong><br>模型调用使用你的 API 额度，必要的商品资料和客户问题会发送到你配置的接口。</p></aside></div>`;
  $("#model-form").addEventListener("submit", (event) => {
    event.preventDefault();
    busy($("button[type=submit]", event.target), async () => {
      const form = new FormData(event.target);
      await post("/api/model", {
        base_url: form.get("base_url"),
        model: form.get("model"),
        api_key: form.get("api_key"),
        vision_model: form.get("vision_model"),
        vision_enabled: form.has("vision_enabled"),
        allow_private: form.has("allow_private"),
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
            `<button class="conversation-item ${item.id === state.conversationId ? "active" : ""}" data-conversation="${item.id}"><div class="flex between"><span class="name">${esc(buyerLabel(item))}</span>${item.needs_human ? '<span class="badge orange">待处理</span>' : item.mode === "human" ? '<span class="badge blue">人工中</span>' : ""}</div><div class="buyer-id">ID：${esc(item.buyer_uid)}</div><div class="preview">${esc(item.preview || "暂无消息")}</div><div class="filemeta">${when(item.updated_at)}${item.assignee_name ? " · " + esc(item.assignee_name) : ""}</div></button>`,
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
  const signature = JSON.stringify(value);
  if (state.conversationSignature === signature) return;
  state.conversationSignature = signature;
  if (state.localTranscript?.id === id) {
    state.localTranscript.messages = mergeTranscript(
      state.localTranscript.messages,
      value.messages,
    );
    state.localTranscript.total = value.total;
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
  const saved =
    Number($("#thread").dataset.id) === id
      ? $("#reply-content")?.value || ""
      : "";
  const previousScroll = Number($("#thread").dataset.id) === id ? threadScrollPosition() : null;
  state.activeMessages = local.messages;
  $("#thread").dataset.id = id;
  $("#thread").innerHTML =
    `<div class="thread-head flex between"><div><h3>${esc(buyerLabel(conversation))}</h3><p class="muted smalltext">用户 ID：${esc(conversation.buyer_uid)} · ${conversation.goods_id ? "商品 " + esc(conversation.goods_id) : "尚未识别具体商品"} · ${conversation.mode === "human" ? "人工接待中" : "AI 辅助接待"} · ${local.total} 条本地记录</p></div><div class="flex">${conversation.mode === "human" && owned ? `<button class="small" data-action="resume" data-id="${id}">交还 AI</button>` : `<button class="primary small" data-action="takeover" data-id="${id}">${conversation.assignee && !owned ? "由其他客服接待" : "接管会话"}</button>`}</div></div>
    ${conversationProduct(local.messages, conversation.goods_id)}
    <div class="chat-messages" id="thread-messages">${local.hasMore ? '<div class="history-load-earlier"><button class="small" data-action="older-local">加载更早记录</button></div>' : '<div class="history-boundary">以上为本地保存的最早记录</div>'}${local.messages.map((message) => timelineMessage(message, buyerLabel(conversation))).join("")}</div>
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
async function loadOlderLocal() {
  const local = state.localTranscript,
    id = state.conversationId;
  if (!local?.hasMore || !local.cursor) return;
  const box = $("#thread-messages"),
    height = box.scrollHeight,
    position = box.scrollTop;
  const value = await api(
    `/api/conversations/${id}?before_id=${local.cursor}&limit=100`,
  );
  if (state.conversationId !== id || state.inboxSource === "history") return;
  local.messages = mergeTranscript(local.messages, value.messages);
  local.hasMore = value.has_more;
  local.cursor = value.next_before_id;
  local.total = value.total;
  local.loadedEarlier = true;
  drawLocalThread();
  $("#thread-messages").scrollTop =
    $("#thread-messages").scrollHeight - height + position;
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
    `<div class="thread-head flex between"><div><h3>${esc(buyerLabel(item))}</h3><p class="muted smalltext">用户 ID：${esc(item.buyer_uid)} · ${transcript.hasMore ? "已加载" : "共"} ${transcript.messages.length} 条历史消息${transcript.hasMore ? " · 还有更早记录" : ""}</p></div><div class="flex"><span class="badge blue">仅浏览</span><button class="small" data-action="history-takeover">接管并回复</button></div></div>
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
    else if (
      state.page === "knowledge" &&
      state.documents.some((item) => item.state === "parsing")
    ) {
      state.documents = await api("/api/documents?shop_id=" + state.shopId);
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
