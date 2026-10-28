from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from time import time
from typing import Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .channels import ChannelError, Channels
from .history import HistoryService, HistoryConflict
from .exports import Exports, ExportConflict
from .message_content import message_preview
from .pdd_protocol import ProtocolError
from .knowledge import ALLOWED, MAX_FILE, MAX_TEXT, Knowledge, file_hash
from .llm import ModelClient, ModelError, validate_endpoint
from .store import Store, password_hash, password_matches, session_hash

ROOT = Path(__file__).resolve().parent


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=40)
    password: str = Field(min_length=1, max_length=200)


class TeamMember(Credentials):
    role: Literal["admin", "agent"] = "agent"


class ShopInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class ExportInput(BaseModel):
    scope: Literal["platform", "local"] = "platform"


class DeleteShopInput(BaseModel):
    confirm_name: str = Field(min_length=1, max_length=100)


class ShopMode(BaseModel):
    mode: Literal["manual", "review", "auto"]


class ModelSettings(BaseModel):
    base_url: str = Field(max_length=500)
    model: str = Field(min_length=1, max_length=200)
    api_key: str = Field(default="", max_length=3000)
    vision_model: str = Field(default="", max_length=200)
    vision_enabled: bool = False
    allow_private: bool = False
    clear_api_key: bool = False


class DocumentInput(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    goods_id: str = Field(default="", max_length=80)
    category: Literal["product", "policy"] = "product"
    content: str = Field(min_length=1, max_length=MAX_TEXT)


class Question(BaseModel):
    shop_id: int
    question: str = Field(min_length=1, max_length=4000)
    goods_id: str = Field(default="", max_length=80)
    history: list[dict[str, str]] = Field(default_factory=list, max_length=10)


class SendInput(BaseModel):
    content: str = Field(min_length=1, max_length=3000)
    request_key: str = Field(min_length=8, max_length=100)


class CookieInput(BaseModel):
    cookies: str = Field(min_length=1, max_length=150000)


class OpenHistoryInput(BaseModel):
    shop_id: int
    buyer_uid: str = Field(min_length=1, max_length=100)
    buyer_name: str = Field(min_length=1, max_length=200)
    chat_type: str = Field(default="", max_length=40)


def create_app(directory=None, model_transport=None, channel_transport=None):
    store = Store(Path(directory or os.environ.get("DESK_DATA_DIR", ROOT.parent / ".data")))
    store.recover_interrupted()
    model = ModelClient(store, model_transport)
    knowledge = Knowledge(store, model)
    channels = Channels(store, knowledge, channel_transport)
    history = HistoryService(store, channels)
    exports = Exports(store, history)
    pending = set()
    attempts = {}

    def background(coro):
        task = asyncio.create_task(coro)
        pending.add(task)
        task.add_done_callback(pending.discard)
        return task

    @asynccontextmanager
    async def lifespan(app):
        yield
        await exports.shutdown()
        await channels.shutdown()
        for task in list(pending):
            task.cancel()
        if pending:
            await asyncio.gather(*list(pending), return_exceptions=True)

    app = FastAPI(title="知店客服工作台", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store, app.state.knowledge, app.state.channels = store, knowledge, channels
    app.state.pending = pending
    app.state.exports = exports

    @app.middleware("http")
    async def request_boundary(request: Request, call_next):
        if request.url.path.startswith("/api/") and request.method not in {"GET", "HEAD", "OPTIONS"}:
            if request.headers.get("X-Desk-Request") != "1":
                return JSONResponse({"detail": "请求来源校验失败，请刷新页面。"}, status_code=403)
            origin = request.headers.get("origin")
            if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
                return JSONResponse({"detail": "不允许跨站修改。"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data: blob: https://*.pddpic.com https://*.yangkeduo.com https://*.pinduoduo.com; "
            "media-src 'self' https://*.pddpic.com https://*.yangkeduo.com https://*.pinduoduo.com; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse({"detail": "提交内容格式不正确，请检查必填项和长度。"}, status_code=422)

    @app.exception_handler(ModelError)
    async def model_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=502)

    @app.exception_handler(ProtocolError)
    async def protocol_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=502)

    @app.exception_handler(ChannelError)
    async def channel_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=502)

    async def current_user(request: Request):
        token = request.cookies.get("desk_session", "")
        user = store.one(
            "SELECT u.id,u.username,u.role FROM sessions s JOIN users u ON u.id=s.user_id "
            "WHERE s.token_hash=? AND s.expires_at>? AND u.active=1",
            (session_hash(token), time()),
        )
        if not user:
            raise HTTPException(401, "请先登录工作台。")
        return user

    async def administrator(user=Depends(current_user)):
        if user["role"] != "admin":
            raise HTTPException(403, "此操作需要管理员权限。")
        return user

    def shop_exists(shop_id):
        shop = store.one("SELECT * FROM shops WHERE id=?", (shop_id,))
        if not shop or shop.get("deleted_at"):
            raise HTTPException(404, "店铺不存在或已删除。")
        return shop

    def document_exists(document_id):
        document = store.one("SELECT * FROM documents WHERE id=?", (document_id,))
        if not document:
            raise HTTPException(404, "资料不存在。")
        shop_exists(document["shop_id"])
        return document

    def conversation_exists(conversation_id):
        row = store.one("SELECT * FROM conversations WHERE id=?", (conversation_id,))
        if not row:
            raise HTTPException(404, "会话不存在。")
        shop_exists(row["shop_id"])
        return row

    def check_password(credentials):
        if len(credentials.password) < 10:
            raise HTTPException(400, "密码至少需要 10 位。")
        if not re.fullmatch(r"[\w@.\-]{1,40}", credentials.username):
            raise HTTPException(400, "用户名可包含汉字、字母、数字、下划线、@、点和短横线。")

    def login_cookie(response, user_id):
        token = secrets.token_urlsafe(40)
        store.execute("DELETE FROM sessions WHERE expires_at<?", (time(),))
        store.execute(
            "INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)",
            (session_hash(token), user_id, time() + 12 * 3600),
        )
        response.set_cookie(
            "desk_session",
            token,
            httponly=True,
            samesite="strict",
            max_age=12 * 3600,
            secure=os.environ.get("DESK_SECURE_COOKIE") == "1",
        )

    @app.get("/api/status")
    async def status():
        return {"initialized": bool(store.one("SELECT id FROM users LIMIT 1"))}

    @app.post("/api/setup")
    async def setup(credentials: Credentials, response: Response):
        check_password(credentials)
        encoded = await asyncio.to_thread(password_hash, credentials.password)
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT id FROM users LIMIT 1").fetchone():
                raise HTTPException(409, "管理员已经创建，请登录。")
            user_id = db.execute(
                "INSERT INTO users(username,password,role,created_at) VALUES(?,?,'admin',?)",
                (credentials.username, encoded, time()),
            ).lastrowid
            db.execute("INSERT INTO shops(name,created_at) VALUES(?,?)", ("我的店铺", time()))
        login_cookie(response, user_id)
        return {"id": user_id, "username": credentials.username, "role": "admin"}

    @app.post("/api/login")
    async def login(credentials: Credentials, request: Request, response: Response):
        remote = request.client.host if request.client else "unknown"
        key = (remote, credentials.username)
        now = time()
        # Opportunistically prune failed-login counters; no secrets or passwords are logged.
        for expired in [item for item, values in attempts.items() if not values or values[-1] < now - 60]:
            del attempts[expired]
        values = [stamp for stamp in attempts.get(key, []) if stamp > now - 60]
        if len(values) >= 6 or sum(len(v) for k, v in attempts.items() if k[0] == remote) >= 30:
            raise HTTPException(429, "登录尝试过于频繁，请一分钟后重试。")
        user = store.one("SELECT * FROM users WHERE username=? AND active=1", (credentials.username,))
        valid = (
            await asyncio.to_thread(password_matches, credentials.password, user["password"])
            if user
            else False
        )
        if not valid:
            attempts[key] = values + [now]
            raise HTTPException(401, "用户名或密码不正确。")
        attempts.pop(key, None)
        login_cookie(response, user["id"])
        return {name: user[name] for name in ("id", "username", "role")}

    @app.post("/api/logout")
    async def logout(request: Request, response: Response, user=Depends(current_user)):
        store.execute(
            "DELETE FROM sessions WHERE token_hash=?",
            (session_hash(request.cookies.get("desk_session", "")),),
        )
        response.delete_cookie("desk_session")
        return {"ok": True}

    @app.get("/api/me")
    async def me(user=Depends(current_user)):
        return user

    @app.get("/api/team")
    async def team(user=Depends(administrator)):
        return store.all("SELECT id,username,role,active,created_at FROM users ORDER BY id")

    @app.post("/api/team")
    async def add_member(member: TeamMember, user=Depends(administrator)):
        check_password(member)
        encoded = await asyncio.to_thread(password_hash, member.password)
        try:
            user_id = store.execute(
                "INSERT INTO users(username,password,role,created_at) VALUES(?,?,?,?)",
                (member.username, encoded, member.role, time()),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(409, "用户名已存在。") from None
        store.audit(user["id"], "team.create", member.username)
        return {"id": user_id}

    @app.post("/api/team/{member_id}/disable")
    async def disable_member(member_id: int, user=Depends(administrator)):
        if member_id == user["id"]:
            raise HTTPException(400, "不能停用自己的账号。")
        with store.connect() as db:
            db.execute("UPDATE users SET active=0 WHERE id=?", (member_id,))
            db.execute("DELETE FROM sessions WHERE user_id=?", (member_id,))
        store.audit(user["id"], "team.disable", str(member_id))
        return {"ok": True}

    @app.get("/api/shops")
    async def shops(user=Depends(current_user)):
        rows = store.all(
            "SELECT id,name,platform_shop_id,platform_user_id,mode,created_at FROM shops WHERE deleted_at IS NULL ORDER BY id"
        )
        for row in rows:
            row["connection"] = channels.states.get(
                row["id"], {"status": "offline", "detail": "尚未连接消息服务。"}
            )
            row["login"] = channels.login_states.get(row["id"], {"status": "idle", "detail": ""})
            row["protocol"] = channels.protocol_report(row["id"])
            if user["role"] == "admin":
                row["export"] = exports.latest(row["id"])
        return rows

    @app.get("/api/shops/deleted")
    async def deleted_shops(user=Depends(administrator)):
        return store.all(
            "SELECT id,name,platform_shop_id,deleted_at FROM shops WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC"
        )

    @app.post("/api/shops/{shop_id}/restore")
    async def restore_shop(shop_id: int, user=Depends(administrator)):
        shop = store.one("SELECT * FROM shops WHERE id=? AND deleted_at IS NOT NULL", (shop_id,))
        if not shop:
            raise HTTPException(404, "已删除店铺不存在。")
        store.execute("UPDATE shops SET deleted_at=NULL WHERE id=?", (shop_id,))
        store.audit(user["id"], "shop.restore", str(shop_id))
        return {"ok": True}

    @app.delete("/api/shops/{shop_id}")
    async def delete_shop(shop_id: int, value: DeleteShopInput, user=Depends(administrator)):
        shop = shop_exists(shop_id)
        if value.confirm_name != shop["name"]:
            raise HTTPException(400, "店铺名称不匹配，请完整输入店铺名称。")
        job = exports.latest(shop_id)
        if job and job["status"] == "running":
            raise HTTPException(409, "店铺正在导出，请先等待完成或暂停导出。")
        with store.connect() as db:
            sending = db.execute(
                "SELECT m.id FROM messages m JOIN conversations c ON c.id=m.conversation_id WHERE c.shop_id=? AND m.state='sending'",
                (shop_id,),
            ).fetchone()
            parsing = db.execute(
                "SELECT id FROM documents WHERE shop_id=? AND state='parsing'", (shop_id,)
            ).fetchone()
            if sending or parsing:
                raise HTTPException(409, "店铺还有正在发送的消息或正在解析的资料，请完成后再删除。")
            db.execute("UPDATE shops SET deleted_at=?,cookies='' WHERE id=?", (time(), shop_id))
        await channels.stop(shop_id)
        await channels.cancel_login(shop_id)
        tasks = [task for key, task in channels.profiles.pending.items() if key[0] == shop_id]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        channels.states.pop(shop_id, None)
        channels.login_states.pop(shop_id, None)
        store.execute("DELETE FROM settings WHERE key=?", (f"pdd.protocol:{shop_id}",))
        store.execute("DELETE FROM pdd_sync WHERE shop_id=?", (shop_id,))
        store.audit(user["id"], "shop.delete", str(shop_id))
        return {"ok": True, "recoverable": True}

    def export_exists(shop_id, export_id):
        shop_exists(shop_id)
        job = store.one("SELECT * FROM exports WHERE id=? AND shop_id=?", (export_id, shop_id))
        if not job:
            raise HTTPException(404, "导出任务不存在。")
        return job

    @app.post("/api/shops/{shop_id}/exports")
    async def start_export(shop_id: int, value: ExportInput, user=Depends(administrator)):
        shop_exists(shop_id)
        job = exports.start(shop_id, value.scope)
        store.audit(user["id"], "shop.export", job["id"])
        return job

    @app.get("/api/shops/{shop_id}/exports/{export_id}")
    async def export_status(shop_id: int, export_id: str, user=Depends(administrator)):
        return export_exists(shop_id, export_id)

    @app.post("/api/shops/{shop_id}/exports/{export_id}/resume")
    async def resume_export(shop_id: int, export_id: str, user=Depends(administrator)):
        job = export_exists(shop_id, export_id)
        try:
            latest = exports.latest(shop_id)
            if latest and latest["status"] == "running" and latest["id"] != export_id:
                raise ExportConflict("此店铺已有运行中的导出任务。")
            exports.resume(job)
        except ExportConflict as exc:
            raise HTTPException(409, str(exc)) from None
        return {"ok": True}

    @app.post("/api/shops/{shop_id}/exports/{export_id}/cancel")
    async def cancel_export(shop_id: int, export_id: str, user=Depends(administrator)):
        export_exists(shop_id, export_id)
        await exports.cancel(export_id)
        return {"ok": True}

    @app.get("/api/shops/{shop_id}/exports/{export_id}/download")
    async def download_export(
        shop_id: int, export_id: str, format: Literal["zip", "markdown"] = "zip", user=Depends(administrator)
    ):
        job = export_exists(shop_id, export_id)
        if job["status"] != "done":
            raise HTTPException(409, "导出尚未完成，请等待完成后再下载。")
        if format == "markdown":
            path = exports.workspace(job["id"]) / "messages.md"
            if not path.is_file():
                raise HTTPException(409, "此旧导出尚未包含 Markdown，请重新导出。")
            return FileResponse(
                path,
                media_type="text/markdown; charset=utf-8",
                filename=f"shop-{shop_id}-conversations-{export_id[:8]}.md",
            )
        path = exports.workspace(job["id"]) / "conversations.zip"
        if not path.is_file():
            raise HTTPException(404, "导出文件已丢失，请新建导出。")
        return FileResponse(
            path, media_type="application/zip", filename=f"shop-{shop_id}-conversations-{export_id[:8]}.zip"
        )

    @app.post("/api/shops")
    async def create_shop(value: ShopInput, user=Depends(administrator)):
        return {
            "id": store.execute(
                "INSERT INTO shops(name,created_at) VALUES(?,?)", (value.name.strip(), time())
            )
        }

    @app.post("/api/shops/{shop_id}/mode")
    async def shop_mode(shop_id: int, value: ShopMode, user=Depends(administrator)):
        shop_exists(shop_id)
        store.execute("UPDATE shops SET mode=? WHERE id=?", (value.mode, shop_id))
        store.audit(user["id"], "shop.mode", f"{shop_id}:{value.mode}")
        return {"ok": True}

    @app.post("/api/shops/{shop_id}/bind")
    async def bind_shop(shop_id: int, value: CookieInput, user=Depends(administrator)):
        shop_exists(shop_id)
        try:
            await channels.bind(shop_id, value.cookies)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        store.audit(user["id"], "shop.bind", str(shop_id))
        return {"ok": True}

    @app.post("/api/shops/{shop_id}/connect")
    async def connect_shop(shop_id: int, user=Depends(administrator)):
        shop_exists(shop_id)
        await channels.start(shop_id)
        store.audit(user["id"], "shop.connect", str(shop_id))
        return {"ok": True}

    @app.post("/api/shops/{shop_id}/disconnect")
    async def disconnect_shop(shop_id: int, user=Depends(administrator)):
        shop_exists(shop_id)
        await channels.stop(shop_id)
        return {"ok": True}

    @app.post("/api/shops/{shop_id}/protocol-check")
    async def protocol_check(shop_id: int, user=Depends(administrator)):
        shop_exists(shop_id)
        report = await channels.check_protocol(shop_id)
        store.audit(user["id"], "shop.protocol_check", str(shop_id))
        return report

    @app.post("/api/shops/{shop_id}/login")
    async def login_shop(shop_id: int, user=Depends(administrator)):
        shop_exists(shop_id)
        await channels.login(shop_id)
        return {"ok": True}

    @app.post("/api/shops/{shop_id}/login-cancel")
    async def cancel_login(shop_id: int, user=Depends(administrator)):
        shop_exists(shop_id)
        await channels.cancel_login(shop_id)
        return {"ok": True}

    @app.post("/api/shops/{shop_id}/login-focus")
    async def focus_login(shop_id: int, user=Depends(administrator)):
        shop_exists(shop_id)
        try:
            await channels.focus_login(shop_id)
        except ChannelError as exc:
            raise HTTPException(409, str(exc)) from None
        return {"ok": True}

    @app.get("/api/model")
    async def get_model(user=Depends(administrator)):
        settings = store.model()
        has_key = bool(settings.pop("api_key", ""))
        return {**settings, "has_key": has_key}

    @app.post("/api/model")
    async def set_model(value: ModelSettings, user=Depends(administrator)):
        values = value.model_dump()
        try:
            values["base_url"] = await asyncio.to_thread(
                validate_endpoint, value.base_url, value.allow_private
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        values["api_key"] = "" if value.clear_api_key else value.api_key or store.model().get("api_key", "")
        values.pop("clear_api_key")
        store.save_model(values)
        store.audit(user["id"], "model.configure", value.model)
        return {"ok": True}

    @app.post("/api/model/test")
    async def test_model(user=Depends(administrator)):
        await model.complete([{"role": "user", "content": "请只回复 OK"}], max_tokens=30)
        return {"ok": True, "detail": "文字模型连接成功。图片能力请上传一张资料图片验证。"}

    @app.get("/api/summary")
    async def summary(shop_id: int, user=Depends(current_user)):
        shop_exists(shop_id)
        counts = store.one(
            "SELECT COUNT(*) total,SUM(state='ready') ready,SUM(state='draft') drafts "
            "FROM documents WHERE shop_id=?",
            (shop_id,),
        )
        conversations = store.one(
            "SELECT COUNT(*) total,SUM(needs_human=1) waiting FROM conversations WHERE shop_id=?", (shop_id,)
        )
        configured = bool(store.model().get("model") and store.model().get("base_url"))
        return {
            "documents": counts["total"],
            "ready_documents": counts["ready"] or 0,
            "draft_documents": counts["drafts"] or 0,
            "conversations": conversations["total"],
            "waiting": conversations["waiting"] or 0,
            "model_configured": configured,
        }

    @app.get("/api/documents")
    async def documents(shop_id: int, user=Depends(current_user)):
        shop_exists(shop_id)
        return store.all(
            "SELECT id,title,filename,goods_id,category,state,error,created_at,length(content) length "
            "FROM documents WHERE shop_id=? ORDER BY created_at DESC",
            (shop_id,),
        )

    @app.post("/api/documents")
    async def upload_document(
        shop_id: int = Form(...),
        goods_id: str = Form(""),
        category: Literal["product", "policy"] = Form("product"),
        file: UploadFile = File(...),
        user=Depends(administrator),
    ):
        shop_exists(shop_id)
        if len(goods_id) > 80:
            raise HTTPException(400, "商品 ID 过长。")
        filename = Path((file.filename or "").replace("\\", "/")).name[:200]
        if Path(filename).suffix.lower() not in ALLOWED:
            raise HTTPException(400, "支持图片、PDF、Word、Excel、CSV、TXT、Markdown。")
        data = await file.read(MAX_FILE + 1)
        await file.close()
        if not data or len(data) > MAX_FILE:
            raise HTTPException(400, "单个文件需在 1 字节至 25 MB 之间。")
        document_id = uuid4().hex
        checksum = file_hash(data)
        try:
            store.execute(
                "INSERT INTO documents(id,shop_id,title,goods_id,category,filename,sha256,created_by,"
                "created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    document_id,
                    shop_id,
                    Path(filename).stem,
                    goods_id.strip(),
                    category,
                    filename,
                    checksum,
                    user["id"],
                    time(),
                ),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(409, "这个商品下已经上传过相同文件。") from None
        try:
            (store.directory / "uploads" / document_id).write_bytes(data)
        except OSError:
            store.execute("DELETE FROM documents WHERE id=?", (document_id,))
            raise HTTPException(507, "资料保存失败，请检查磁盘空间。") from None
        background(knowledge.parse_document(document_id))
        store.audit(user["id"], "document.upload", document_id)
        return {"id": document_id}

    @app.get("/api/documents/{document_id}")
    async def get_document(document_id: str, user=Depends(current_user)):
        document = document_exists(document_id)
        document.pop("sha256")
        return document

    @app.get("/api/documents/{document_id}/file")
    async def get_document_file(document_id: str, user=Depends(current_user)):
        document = document_exists(document_id)
        return FileResponse(
            store.directory / "uploads" / document_id,
            filename=document["filename"],
            media_type="application/octet-stream",
        )

    @app.post("/api/documents/{document_id}/publish")
    async def publish_document(document_id: str, value: DocumentInput, user=Depends(administrator)):
        document = document_exists(document_id)
        if document["state"] == "parsing":
            raise HTTPException(409, "正在识别资料，请等待完成。")
        if not value.content.strip() or not value.title.strip():
            raise HTTPException(400, "资料标题和内容不能为空。")
        try:
            count = knowledge.publish(
                document_id, value.title.strip(), value.goods_id.strip(), value.category, value.content
            )
        except sqlite3.IntegrityError:
            raise HTTPException(409, "相同商品下已存在这份资料。") from None
        store.audit(user["id"], "document.publish", document_id)
        return {"ok": True, "chunks": count}

    @app.post("/api/documents/{document_id}/retry")
    async def retry_document(document_id: str, user=Depends(administrator)):
        document = document_exists(document_id)
        if document["state"] not in {"draft", "failed"}:
            raise HTTPException(409, "只有待审核或失败的资料可以重新识别。")
        store.execute("UPDATE documents SET state='parsing',error='' WHERE id=?", (document_id,))
        background(knowledge.parse_document(document_id))
        return {"ok": True}

    @app.delete("/api/documents/{document_id}")
    async def delete_document(document_id: str, user=Depends(administrator)):
        document = document_exists(document_id)
        if document["state"] == "parsing":
            raise HTTPException(409, "正在识别中，请完成后再删除。")
        store.execute("DELETE FROM documents WHERE id=?", (document_id,))
        (store.directory / "uploads" / document_id).unlink(missing_ok=True)
        store.audit(user["id"], "document.delete", document_id)
        return {"ok": True}

    @app.post("/api/playground")
    async def playground(value: Question, user=Depends(current_user)):
        shop_exists(value.shop_id)
        if not value.question.strip():
            raise HTTPException(400, "请输入问题。")
        return await knowledge.answer(value.shop_id, value.question, value.goods_id, value.history)

    @app.get("/api/history/conversations")
    async def history_conversations(
        shop_id: int,
        page: int = Query(1, ge=1, le=10000),
        size: int = Query(20, ge=1, le=50),
        user=Depends(current_user),
    ):
        shop_exists(shop_id)
        return await history.conversations(shop_id, page, size)

    @app.get("/api/history/messages")
    async def history_messages(
        shop_id: int,
        buyer_uid: str = Query(..., min_length=1, max_length=100),
        before: str = Query("", max_length=100),
        size: int = Query(50, ge=1, le=100),
        chat_type: str = Query("", max_length=40),
        user=Depends(current_user),
    ):
        shop_exists(shop_id)
        return await history.messages(shop_id, buyer_uid, before, size, chat_type)

    @app.post("/api/history/open")
    async def open_history(value: OpenHistoryInput, user=Depends(current_user)):
        shop_exists(value.shop_id)
        try:
            return await history.open_for_reply(
                value.shop_id, value.buyer_uid, value.buyer_name, user, value.chat_type
            )
        except HistoryConflict as exc:
            raise HTTPException(409, str(exc)) from None

    @app.get("/api/conversations")
    async def conversations(shop_id: int, user=Depends(current_user)):
        shop_exists(shop_id)
        items = store.all(
            "SELECT c.*,u.username assignee_name,(SELECT content FROM messages m "
            "WHERE m.conversation_id=c.id ORDER BY m.created_at DESC,m.id DESC LIMIT 1) preview, "
            "(SELECT presentation FROM messages m WHERE m.conversation_id=c.id "
            "ORDER BY m.created_at DESC,m.id DESC LIMIT 1) preview_presentation "
            "FROM conversations c LEFT JOIN users u ON u.id=c.assignee WHERE c.shop_id=? "
            "ORDER BY c.updated_at DESC LIMIT 200",
            (shop_id,),
        )

        for item in items:
            item["preview"] = message_preview(item["preview"], item.pop("preview_presentation", None))
        return await channels.profiles.enrich(shop_id, items)

    @app.get("/api/conversations/{conversation_id}")
    async def conversation_detail(
        conversation_id: int,
        limit: int = Query(200, ge=1, le=200),
        before_id: int | None = Query(None, ge=1),
        user=Depends(current_user),
    ):
        conversation = conversation_exists(conversation_id)
        conversation["buyer_name"] = await channels.profiles.resolve(
            conversation["shop_id"],
            conversation["buyer_uid"],
            conversation["platform_chat_type"],
            fallback=conversation["buyer_name"],
        )
        condition = "m.conversation_id=?"
        parameters = [conversation_id]
        if before_id is not None:
            anchor = store.one(
                "SELECT id,created_at FROM messages WHERE id=? AND conversation_id=?",
                (before_id, conversation_id),
            )
            if not anchor:
                raise HTTPException(404, "消息分页位置不存在。")
            condition += " AND (m.created_at<? OR (m.created_at=? AND m.id<?))"
            parameters.extend([anchor["created_at"], anchor["created_at"], anchor["id"]])
        messages = store.all(
            "SELECT m.*,u.username author FROM messages m LEFT JOIN users u ON m.created_by=u.id "
            "WHERE " + condition + " ORDER BY m.created_at DESC,m.id DESC LIMIT ?",
            parameters + [limit + 1],
        )
        has_more = len(messages) > limit
        messages = messages[:limit][::-1]
        await history.enrich_local_messages(conversation, messages)
        for message in messages:
            message["sources"] = json.loads(message["sources"])
        total = store.one("SELECT COUNT(*) n FROM messages WHERE conversation_id=?", (conversation_id,))["n"]
        return {
            "conversation": conversation,
            "messages": messages,
            "total": total,
            "has_more": has_more,
            "next_before_id": messages[0]["id"] if has_more and messages else None,
        }

    @app.post("/api/conversations/{conversation_id}/takeover")
    async def takeover(conversation_id: int, user=Depends(current_user)):
        conversation = conversation_exists(conversation_id)
        if conversation["assignee"] not in {None, user["id"]} and user["role"] != "admin":
            raise HTTPException(409, "会话已由其他客服接管。")
        # Deliberately does not wait for the generation lock: an in-flight AI answer must observe this change.
        with store.connect() as db:
            changed = db.execute(
                "UPDATE conversations SET mode='human',assignee=?,needs_human=0 WHERE id=? "
                "AND (assignee IS NULL OR assignee=? OR ?='admin')",
                (user["id"], conversation_id, user["id"], user["role"]),
            ).rowcount
            if not changed:
                raise HTTPException(409, "会话已由其他客服接管。")
        return {"ok": True}

    @app.post("/api/conversations/{conversation_id}/resume")
    async def resume(conversation_id: int, user=Depends(current_user)):
        async with channels.lock(conversation_id):
            conversation = conversation_exists(conversation_id)
            if conversation["assignee"] not in {None, user["id"]} and user["role"] != "admin":
                raise HTTPException(409, "只能交还自己接管的会话。")
            store.execute(
                "UPDATE conversations SET mode='ai',assignee=NULL,needs_human=0 WHERE id=?",
                (conversation_id,),
            )
        return {"ok": True}

    @app.post("/api/conversations/{conversation_id}/send")
    async def send_message(conversation_id: int, value: SendInput, user=Depends(current_user)):
        async with channels.lock(conversation_id):
            conversation = conversation_exists(conversation_id)
            if conversation["assignee"] != user["id"]:
                raise HTTPException(409, "请先接管会话再回复。")
            if not value.content.strip():
                raise HTTPException(400, "回复不能为空。")
            result = await channels.send(
                conversation_id,
                value.content.strip(),
                user["id"],
                f"user:{conversation_id}:{value.request_key}",
            )
        if result["state"] != "sent":
            raise HTTPException(409, "此前发送结果未确认，请先在商家后台核对。")
        return {"ok": True, "id": result["id"]}

    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

    @app.get("/")
    async def index():
        return FileResponse(ROOT / "static" / "index.html")

    return app
