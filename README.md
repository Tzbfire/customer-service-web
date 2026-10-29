# 知店 · 拼多多商家 AI 客服系统

面向同一商家团队的网页客服工作台，支持多个店铺、自有大模型接口、商品知识和人工协作。应用代码在 `customer-service-web/`，运行时不依赖旁边克隆的参考项目。

文档核对日期：2026-10-29；对应功能基线提交：`d7bc3f1`。

## 启动

安装 uv 与受支持的 Python（`>=3.12,<3.14`）后，从仓库根目录运行：

```powershell
cd customer-service-web
uv sync --frozen
uv run --frozen python -m desk
```

访问 `http://127.0.0.1:8765/`，首次自行创建管理员。Windows 也可双击应用目录的 `启动.cmd`，使用期间保留服务窗口。店铺登录在服务电脑的独立 Chrome / Edge 完成；服务重启后需在“店铺连接”重新连接消息。

## 当前功能

| 入口 | 能力 |
| --- | --- |
| 店铺连接 | 独立浏览器登录、协议检查、四种接待方式、可恢复删除 |
| 接待工作台 | 实时接收、自动补齐平台记录、更早分页、历史浏览、人工接管、订单查看 |
| 消息展示 | 昵称及 ID、图片缩略图/放大、商品卡片、订单卡片、视频入口和系统提示 |
| 知识库 | 混合格式上传与审核、在售商品及 SKU 同步、详情图识别、聊天＋订单总结 |
| 对话导出 | 平台可获取历史＋本地记录，ZIP / CSV / JSON / HTML / Markdown，支持暂停和继续 |
| 模型配置 | Chat Completions 兼容接口、多模态图片识别、本地关键词开关 |
| 团队成员 | 管理员与客服权限、同一商家共享店铺 |

接待方式为“人工接待”“AI 草稿，人工审核发送”“有依据时自动回复”“AI 接管”。AI 接管绕过本地关键词筛选，模型根据商品资料和上下文接待新文字咨询；人工接管优先，不对历史补齐主动回复。现有店铺不会因新增模式自动启用。

商品结构化资料同步后自动入库；上传资料、详情图识别和聊天订单总结需审核后参与回答。Nuwa 客服 Skill 蒸馏尚未接入，也没有模型训练或微调。

完整用法、模式区别、数据范围和部署说明见 [使用指南](customer-service-web/README.md)；实测依据与未验证边界见 [协议与验证记录](customer-service-web/PROTOCOL-AUDIT.md)。

## 验证

从 `customer-service-web/` 运行：

```powershell
uv run pytest -q
uv run ruff check desk tests scripts
node --check desk/static/app.js
node --check desk/static/messages.js
node --test tests/test_message_rendering.mjs
```

最近完整回归结果为 141 项后端、6 项前端通过；本次文档核对确认仍可收集 141 项后端测试，6 项前端渲染测试重新运行通过。自动测试使用模拟平台/模型，不等于真实消息发送验证。

2026-10-28 至 2026-10-29 已用真实登录态核对鉴权、心跳、消息接收、历史分页、图片与商品卡片、商品同步、模型引用及历史订单关联。真实回复发送尚缺专用测试会话验证，AI 接管也未向真实买家试发。不要将“已连接”理解为所有发送路径都已验证。

## 数据与参考项目

运行数据默认保存在 `customer-service-web/.data/`，包括数据库、上传原件、登录态、密钥、导出包和任务检查点。API Key、Cookie、买家聊天/订单、截图及原始协议证据不随 Git 提交。备份与迁移需保留整个数据目录，尤其是 `secret.key`。

本地参考项目独立管理，不是运行依赖：

- `JC0v0/Customer-Agent`：协议参考提交 `4343a1f245f3a5287497c583523a68430d3e2b0d`。
- `mf-yang/pdd-customer-service-sdk`：比较参考提交 `92ba11d6baee45c4f8586e90751e2b5c8537c28f`。
- `ChatGPT-On-CS/`：另行克隆的参考项目，本应用未导入或运行它。
- `nuwa-skill/`：后续蒸馏方法参考，本应用尚未接入或执行该 Skill。

当前可运行应用仍是 `customer-service-web/`；`merchant-desk/` 是暂停整理的独立副本，不用于当前启动。
