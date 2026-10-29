# 拼多多接入协议与验证记录

文档核对日期：2026-10-29。覆盖 2026-10-28 至 2026-10-29 已记录的运行验证，以及功能基线提交 `d7bc3f1` 的实现。本文是接入记录，不表示所有平台接口已验证。

## 当前结论与验证层级

采用已验证可鉴权的旧 JSON WebSocket 通道，结合 HTTP 序号恢复、会话历史补齐与只读商品/订单接口。已观察真实消息并验证部分历史与媒体；早期检查期间“未收到买家消息”的结论仅适用于当时的短时测试。初期分析保存在 [PROTOCOL-AUDIT-initial.md](PROTOCOL-AUDIT-initial.md)，不能当作当前代码状态。

| 验证项 | 当前证据 |
| --- | --- |
| 身份与登录 | 商家/客服身份接口、独立可见 Chrome / Edge 登录流程已运行核对 |
| 构建与 JSON 通道 | 已记录构建 `202610261554` 的鉴权、心跳、序号同步成功；运行时从带登录态工作台资源路径发现版本 |
| 实际接收与历史 | 已接收买家和商家消息；历史会话、消息分页、实时缺口补齐已有真实记录核对 |
| 昵称、商品卡、图片 | 昵称及 ID、商品卡片、客户端与买家上传图片、放大预览已验证 |
| 商品资料与模型 | 商品列表/详情/SKU 同步、商品 ID 隔离与真实模型引用验证成功；一款商品详情图识别成功 |
| 订单 | 买家近期与更早订单、商品/规格/数量/时间/支付状态及同款历史多笔购买已验证 |
| 聊天＋订单总结 | 模拟模型覆盖完整流程与引用核验；未执行真实全量总结或客服 Skill 蒸馏 |
| 回复发送与 AI 接管 | 代码与模拟平台覆盖发送确认、幂等、转人工；尚缺专用真实会话发送验证，不能用接收成功推断发送成功 |
| Titan 二进制 | 当前不支持；收到不识别二进制帧明确报错，不静默当作 JSON |

最近完整回归为 141 项后端、6 项前端通过。本次更新核对文档、代码、链接和 141 项后端测试收集，重跑 6 项前端渲染测试通过；没有重新执行完整后端回归或真实平台收发。

## 登录与请求身份

店铺登录态由独立浏览器绑定，WebSocket token 与 Cookie 不返回给普通页面，也不写入本文。身份验证使用 `/janus/api/new/userinfo`、`/earth/api/merchant/queryMerchantInfoByMallId`；实测 `success: true, errorCode: 1000000` 是可接受成功形态。

HTTP 适配器识别过期、额外验证、拒绝、重定向和异常结构；错误不带原始 Cookie、token 或完整客户内容。不同店铺、客服身份与商品归属按对应模块核对，不因字段名称看似一致就混用。

登录成功判定使用实际域名和路径；登录页参数里的 `redirectUrl=...chat-merchant...` 不代表已经进入工作台。当前为真实可见浏览器输入，不使用截图输入代理。

## 接收通道与恢复

- `POST /chats/getToken`，JSON `{"version":3}` 获取旧通道 token。
- 连接 `wss://m-ws.pinduoduo.com/`，使用 `access_token`、`role=mall_cs`、`client=web`、自动发现的 `version`。
- 已验证鉴权帧形态包含 `response=auth`、`auth.result=ok`、`result=ok`、`status=1`。等待最多 12 秒，拒绝或矛盾状态失败。
- 每 25 秒发送 `cmd=heartbeat`，以 `request_id` 匹配回包，最多等 12 秒。
- `POST /plateau/sync/get_init_state`，body `{"client_type":"WEB"}` 建立同步基线。观察到类型 1、10，当前只消费类型 1 聊天同步。
- `POST /plateau/sync/message`，body `{"sync_key":[{"seq_type":1,"seq_id":游标}]}`，每 15 秒检查增量，先保存再推进游标。
- 分页不前进、有缺口、回退、单轮超过 50 页会明确报错，连接重试；账号身份变化清理对应同步游标。

首次连接从当前序号建立基线，不全量导入历史。`decode_push` 保存可识别买家归属的买家/商家/系统消息；已读同步不作为聊天展示。恢复消息标记非自动处理，不主动向旧咨询发回复。

运行中构建号自动缓存一小时；本文所列构建是观察值，不是固定配置或永远最新版本。新子系统 17 / Titan 的 token 不与旧 JSON 通道混用。

## 历史、昵称与实时补齐

| 接口 | 已验证用途与字段 |
| --- | --- |
| `/plateau/chat/latest_conversations` | `data.cmd=latest_conversations`、`version=2`、`need_unreply_time=true`、`page`、`size`，外层 `client=WEB`；返回 `result.conversations` 和 `has_more`，准确总数可能缺失 |
| `/plateau/chat/list` | `data.cmd=list`，`list.with={role:user,id:买家ID}`、`start_index=0`、`size`、可选 `start_msg_id` / `chat_type`，`notUpdateUnreplyTs=true`，外层 `client=WEB` |
| `/latitude/user/userInfoWithTags` | `uid`，可选 `chatType`；匹配 `result.uid` 后提取 `nickname`，不保存其他买家资料 |

保留消息 ID 为字符串，避免 JavaScript 大整数精度损失；历史原始条目可能是最后一条消息本身。买家昵称读取绑定买家的 `user_info.nickname` 或 user actor；不能把最后发送消息的客服昵称赋给买家。

历史标签仅浏览时不导入消息、不调用模型、不改变接待模式或序号，昵称缓存等显示信息可补齐。明确“接管并回复”才导入最新页并设人工接待。

实时接待使用 `transcript_sync` 检查点，打开时补取最近 50 条、短暂复用检查结果，按平台 ID 合并并支持更早分页。中间断线缺口与更早游标分别维护，不仅凭本地边界宣称完整。两条真实会话从 12→42、5→12，平台消息 ID 无遗漏，接待状态不变且未生成 AI 回复。

按原时间排序与选取上下文，避免旧消息后插入压过新咨询。页面补齐可能先于实时推送；只有 120 秒内的新鲜实时买家推送可以领取对应历史条目一次进行正常处理，历史拉取本身不触发。撤回状态不会被原文回填覆盖。

## 商品、订单及知识关联

| 接口 | 请求 / 校验 |
| --- | --- |
| `/latitude/goods/recommendGoods` | `uid=""`、`pageNum`、`pageSize`，读取 `onSaleGoods` 与 `total` |
| `/glide/v2/mms/query/commit/on_shop/detail` | `goods_id`，详情必须匹配请求 ID 与当前店铺 `mall_id`；读取名称、描述、分类、SKU、详情图 |
| `/latitude/order/userAllOrder` | `uid`、`pageNo`、`pageSize`、`showHistory=true`，返回近期 `orders` / `total` 和更早 `historyTotal` / `historyTotalPeriodCode` |
| `/latitude/order/userHistoryOrder` | `uid`、`historyTotalPeriodCode`、`pageNo`、`pageSize`，单独读取更早 `orders` / `total` |

历史订单入口从当前商家弹窗模块加载资源中定位后，再用真实只读请求核对；不直接把 `historyTotal` 当作已读取订单。新旧订单使用驼峰/下划线两种字段，按买家与店铺归属验证、订单号去重，历史可能缺少 SKU ID。

有效支付购买在当前实现中要求 `pay_status=2`、`order_status=1`、`group_status` 为 1 或未提供，数量大于 0；保留原状态，不由这些标记保证无售后。时间不足、订单未取全时不能判断无复购。数量按规格分别计算，不从多件一单推断多次下单，也不反推商品通用耗量。

商品卡片 `goodsPrice` / `couponPromoPrice` 的已观察值为元；商品同步列表的 `minOnSaleGroupPrice` / `maxOnSaleGroupPrice` 及详情 SKU `multi_price` 按分处理。禁止将所有同名“价格”字段统一换算。

已记录样本包括 5 个商品、101 个在售 SKU、41 个详情图地址；另一只读验证读到同一买家近期 1 笔＋历史 3 笔、同款 3 笔支付订单均早于可见咨询。此为有限样本，不代表所有商品或无限期订单历史。

聊天＋订单总结使用最近完整平台导出的本地快照。面积逐字引用买家消息、历史问答逐字引用客服消息后才能保存，原文映射留在本地。结果先待审核，不直接训练模型或改变客服 Skill。该总结流程的模型执行目前只经模拟测试验证。

## 媒体解析与加载策略

统一规范化器处理图片 `type=1`、文字/商品、订单、视频、表情、系统菜单及撤回。商品、订单和未知卡片按实际字段展示，不能展示的保留摘要。

图片来自 `content` 或已观察到的 `info.image_url` 等字段。客户端与买家上传图片已确认使用精确 HTTPS 主机 `chat-img.pddugc.com`，现已同时加入后端解析、前端预览和 `img-src`。保留既有 `pddpic.com` / `yangkeduo.com` / `pinduoduo.com` 媒体策略，不放开任意 `pddugc.com` 子域；拒绝带账号密码、非允许端口和伪域名。3 张真实上传图返回 JPEG，网页缩略图和放大已验证。

撤回依据 `type=1002,info.update_type=3,info.msg_id` 定位目标；同 ID / 乱序事件和跨页回填由测试覆盖。常见问题菜单提取 `info.content[].text`，不执行其中 `click_action`。视频、独立订单卡与撤回尚不能以模拟测试冒充所有真实样本验证。

## 发送与 AI 接管边界

`POST /plateau/chat/send_message` 使用 JSON 命令、真实 `chat_type`（如有）、普通时间戳及已读状态字段，记录 `sending/sent/uncertain`。当前没有接入前端的 `anti_content`、`hash`、`random` 等来源；不能在未验证前猜造或断言都非必需。

四种接待方式见 [使用指南](README.md#接待方式与人工协作)。AI 接管绕过关键词筛选，模型有效引用的事实回答以及模型标为不含商品事实的问候/澄清可以进入发送路径；人工接管优先。模式与引用检查由测试覆盖，模型自分类不构成对事实正确性的保证。

尚未进行专用真实买家试发，界面“发送尚未验证”与本文一致。鉴权、心跳、接收、图片显示或模拟发送成功均不能替代实际发送验证。

## 复查方法与本地证据

不涉及真实账号的检查：

```powershell
uv run pytest -q
uv run ruff check desk tests scripts
node --check desk/static/app.js
node --check desk/static/messages.js
node --test tests/test_message_rendering.mjs
```

一般真实协议复查：管理员在“店铺连接”先断开目标消息连接，再运行“检查协议”，只检查构建、鉴权、心跳及同步初始化，不发送买家消息。完成后按需重新连接。

开发脚本有额外前提：

- `scripts/audit_pdd_protocol.py --require-build-match` 只比较本地已捕获资源与适配器；依赖 `evidence/protocol-2026-10-28/` 的脚本/清单，新克隆不包含这些忽略文件。它读取历史探测报告，不是重新认证平台的测试。
- `python -m scripts.verify_pdd_receiver` 固定读取 `.data` 的店铺 ID 1，使用隔离数据库和计数回调，不调用模型或发送业务消息。只适用于该 ID 仍绑定有效账号、且正式连接已停止的开发环境；不能用于任意当前店铺。

历史协议资源与探测摘要位于本机 `evidence/protocol-2026-10-28/`，后续验证计数、图片、交易字段样本、备份与导出位于 `.data/`。均由 Git 忽略，可能含商家或买家信息，不应公开。本文只记录能力、字段和汇总，不写 Cookie、token、买家标识、订单编号或聊天正文。
