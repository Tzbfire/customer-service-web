# 拼多多协议核对记录（2026-10-28）

结论：当前接入实现需要补充适配和真实链路验证。已确认前端实现与本地简化协议存在差异；尚不能据此断言旧接口已停用，也不能宣称新系统已完成拼多多收发实测。

## 本次取得的证据

- 用户在 Codex 内置浏览器登录了商家客服工作台，页面地址为 `https://mms.pinduoduo.com/chat-merchant/index.html#/`。
- 从该已登录页面的脚本标签读取到构建目录 `chat-merchant-v20261026.15.54.13`，随后下载对应公开脚本并记录 SHA-256。
- 主脚本中的 `window.SERVICE_BUILD_TIME` 为 `202610261554`。
- 本地网页系统与 Customer-Agent 仓库仍使用固定值 `202506091557`。远端仓库 HEAD 本次核对仍是 `4343a1f245f3a5287497c583523a68430d3e2b0d`。

脚本来源和哈希见 `evidence/protocol-2026-10-28/live-assets.json`。原始文件与格式化衍生文件分别保存；公开脚本不含本次用户的 Cookie 或 token。

## 对照结果

| 部分 | 当前已加载脚本中的实现 | 本地实现与影响 |
| --- | --- | --- |
| 旧连接链路 | 仍保留 `/chats/getToken`，请求 `version: 3`；旧 `m-ws` URL 的版本从 `window.SERVICE_BUILD_TIME` 读取 | 本地仍固定旧构建值，需要改为发现当前版本。不能仅因日期不同认定服务端拒绝旧值 |
| 新鉴权链路 | IMSDK 初始化调用 `/janus/api/subSystem/getAuthToken`，`subSystemId: 17`；连接参数包含 `authType: 4`、`sceneType: 1001` | 本地仅实现旧链路，未覆盖这条鉴权流程 |
| 新接收链路 | Titan 连接采用 `arraybuffer`，会话建立使用 `sessionResumptionReq`，并有初始化同步、序号同步和消息同步 | 本地只处理 JSON 文本推送，不能作为 Titan 二进制接入实现 |
| 消息同步 | `/plateau/sync/get_init_state`、`/plateau/sync/message`，结合 `seq_id`、`seq_type` | 本地没有对应序号同步，重连可靠性需要另行验证 |
| 文字发送 | `/plateau/chat/send_message` 仍存在；当前命令发送构造包含 `anti_content`、`hash`、`random`、`ts`、`chat_type` 等字段 | 本地发送结构更简化。字段有差异，但还没有服务端响应证明每一项都必填；不能凭源码猜值补齐 |
| 其他发送形式 | IMSDK 还提供含 `client_msg_id`、`conv_id`、`chat_type_id` 的消息结构 | 应按实际运行分支适配，避免把两套结构混在一起 |

原始主脚本 SHA-256：`a6981e869bbaf9a95a2e0e9875c9b31b4bc04fa18f92d82867623f18dd8e3a0e`。

## 代码定位

以下行号对应 `evidence/protocol-2026-10-28/derived-index.pretty.js`：

- 10117：当前构建版本赋值。
- 68391：IMSDK 初始化。
- 68598：旧 `getToken` 链路仍在客户端初始化代码中。
- 70046：新 IMSDK 初始化参数及 Titan 地址选择。
- 112677：二进制会话建立。
- 115076：初始化消息序号同步。
- 115100：子系统鉴权接口。
- 115255：子系统 17，随后设置 `authType: 4`、`sceneType: 1001`。
- 43506：当前命令式发送结构。
- 115046：另一种 IMSDK 消息发送结构。

## 尚未验证的部分

当前取得的是已加载脚本与页面状态；尚未取得该账号真实的 WebSocket 握手、鉴权响应、消息帧，以及发送响应。网络取证工具当前连接的是另一个空白浏览器，不能将它的结果当作用户已登录工作台的流量。

因此，本次不声称已验证该账号实际选中的灰度分支、不声称旧链路已失效，也不声称所有缺失字段都被服务器强制要求。没有向真实买家发送测试消息。

在新客服系统内绑定店铺后，可以由实际后端先验证只读身份与鉴权、旧连接是否被接受，再对照新链路决定迁移方式。Codex 内置浏览器的登录态不会自动成为新系统的登录态。真实发送验证应在指定测试会话中进行。

## 复现静态核对

```powershell
uv run python scripts/audit_pdd_protocol.py
uv run python scripts/audit_pdd_protocol.py --require-build-match
```

第一条输出可核对的构建版本、两套链路和发送字段差异；第二条在当前固定版本不一致时返回退出码 1。这是源码差异检查，不是鉴权与收发通过测试。

本轮仅新增取证文件与核对脚本，没有仅凭未验证的猜测修改生产发送参数或切换接待模式。
