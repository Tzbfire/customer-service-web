# 知店 · 拼多多商家 AI 客服系统

网页版客服工作台，支持商家自有大模型接口、商品资料知识库和多人协作。

应用代码在 `customer-service-web/`。使用说明见 [customer-service-web/README.md](customer-service-web/README.md)，协议验证说明见 [customer-service-web/PROTOCOL-AUDIT.md](customer-service-web/PROTOCOL-AUDIT.md)。

## 启动

```powershell
cd customer-service-web
uv sync --frozen
uv run python -m desk
```

访问 `http://127.0.0.1:8765`，首次使用自行创建管理员账号。在系统里配置模型、上传资料、审核入库，并使用独立 Chrome / Edge 登录店铺。

## 验证

```powershell
cd customer-service-web
uv run pytest -q
uv run ruff check desk tests scripts
node --check desk/static/app.js
node --test tests/test_message_rendering.mjs
```

真实身份、鉴权、心跳、增量同步、买家消息接收及历史会话已在 2026-10-28 验证。昵称、图片预览与商品卡片已用真实记录验证；后端 68 项测试及前端 5 项测试通过。实际回复发送仍需专用测试会话验证。

## 参考项目

本地另外克隆的两个仓库仅用于分析比较，未作为嵌套仓库提交：

- `JC0v0/Customer-Agent`：参考提交 `4343a1f245f3a5287497c583523a68430d3e2b0d`。
- `mf-yang/pdd-customer-service-sdk`：参考提交 `92ba11d6baee45c4f8586e90751e2b5c8537c28f`。

运行数据、API Key、Cookie、登录浏览器配置、数据库、备份和本地协议抓取证据均被 Git 忽略。协议核对文档中的证据路径指向开发机器上的本地文件，新克隆的代码仓库不包含这些文件。
