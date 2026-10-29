"""Resumable read-only transcript exports; no credentials or model calls in the archive."""

from __future__ import annotations

import asyncio
import csv
import html
import hashlib
import json
import sqlite3
import zipfile
from datetime import datetime, timedelta, timezone
from time import time
from uuid import uuid4

from .message_content import read_presentation
from .export_markdown import heading, conversation_header, message_markdown
from .pdd_protocol import ProtocolError
from .export_incremental import select_messages


class ExportConflict(Exception):
    pass


class Exports:
    def __init__(self, store, history):
        self.store, self.history = store, history
        self.root = store.directory / "exports"
        self.root.mkdir(exist_ok=True)
        self.tasks = {}
        store.execute(
            "UPDATE exports SET status='interrupted',error=? WHERE status='running'",
            ("服务重启中断导出，可继续任务。",),
        )

    def latest(self, shop_id):
        return self.store.one(
            "SELECT * FROM exports WHERE shop_id=? ORDER BY (status='running') DESC,created_at DESC LIMIT 1",
            (shop_id,),
        )

    def last_success(self, shop_id, scope):
        return self.store.one(
            "SELECT * FROM exports WHERE shop_id=? AND scope=? AND status='done' "
            "ORDER BY updated_at DESC,rowid DESC LIMIT 1",
            (shop_id, scope),
        )

    def baselines(self, shop_id):
        result = {}
        for scope in ("platform", "local"):
            job = self.last_success(shop_id, scope)
            if job:
                result[scope] = {"id": job["id"], "at": job["updated_at"]}
        return result

    def update(self, identifier, **values):
        self.store.execute(
            "UPDATE exports SET " + ",".join(key + "=?" for key in values) + ",updated_at=? WHERE id=?",
            (*values.values(), time(), identifier),
        )

    def start(self, shop_id, scope, mode="full"):
        latest = self.latest(shop_id)
        if latest and latest["status"] == "running":
            return latest
        if scope == "platform":
            self.history.channels.cookies(shop_id)
        baseline = self.last_success(shop_id, scope)
        if mode == "incremental" and baseline and not self.snapshot_path(baseline["id"]).is_file():
            raise ExportConflict("上次成功导出的消息基准文件已丢失，请先全量导出以重建基准。")
        identifier = uuid4().hex
        self.store.execute(
            "INSERT INTO exports(id,shop_id,scope,status,mode,baseline_id,baseline_at,created_at,updated_at) "
            "VALUES(?,?,?,'running',?,?,?,?,?)",
            (
                identifier,
                shop_id,
                scope,
                mode,
                baseline["id"] if baseline else None,
                baseline["updated_at"] if baseline else None,
                time(),
                time(),
            ),
        )
        self.tasks[identifier] = asyncio.create_task(self.run(identifier))
        return self.store.one("SELECT * FROM exports WHERE id=?", (identifier,))

    def resume(self, job):
        if job["status"] == "running":
            return
        if job["status"] == "done":
            raise ExportConflict("任务已完成，可直接下载或新建导出。")
        current = self.last_success(job["shop_id"], job["scope"])
        if current and current["id"] != job.get("baseline_id") and current["updated_at"] > job["created_at"]:
            raise ExportConflict("此任务之后已有成功导出，请新建增量导出，避免重复记录。")
        self.update(job["id"], status="running", error="")
        self.tasks[job["id"]] = asyncio.create_task(self.run(job["id"]))

    def workspace(self, identifier):
        folder = self.root / identifier
        folder.mkdir(exist_ok=True)
        return folder

    def snapshot_path(self, identifier):
        return self.root / identifier / "transcripts.sqlite3"

    def connect(self, identifier):
        db = sqlite3.connect(self.workspace(identifier) / "transcripts.sqlite3")
        db.row_factory = sqlite3.Row
        db.executescript("""
            CREATE TABLE IF NOT EXISTS contacts (
              uid TEXT NOT NULL, chat_type TEXT NOT NULL, name TEXT NOT NULL,
              cursor TEXT NOT NULL DEFAULT '', done INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY(uid,chat_type));
            CREATE TABLE IF NOT EXISTS discovered_pages (page INTEGER PRIMARY KEY, signature TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS transcript (
              uid TEXT NOT NULL, chat_type TEXT NOT NULL, key TEXT NOT NULL,
              platform_id TEXT, ts REAL NOT NULL, payload TEXT NOT NULL,
              PRIMARY KEY(uid,chat_type,key));
        """)
        columns = {row[1] for row in db.execute("PRAGMA table_info(transcript)")}
        if "included" not in columns:
            db.execute("ALTER TABLE transcript ADD COLUMN included INTEGER NOT NULL DEFAULT 1")
        if "export_change" not in columns:
            db.execute("ALTER TABLE transcript ADD COLUMN export_change TEXT NOT NULL DEFAULT 'new'")
        return db

    def add_messages(self, db, uid, chat_type, messages, source):
        for message in messages:
            value = {
                k: message.get(k)
                for k in ("platform_id", "role", "content", "created_at", "state", "presentation")
            }
            value["presentation"] = read_presentation(value.get("presentation"))
            value["source"] = source
            key = (
                "p:" + str(message["platform_id"])
                if message.get("platform_id")
                else "local:" + str(message["id"])
            )
            db.execute(
                "INSERT INTO transcript(uid,chat_type,key,platform_id,ts,payload) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(uid,chat_type,key) DO UPDATE SET payload=excluded.payload,ts=excluded.ts",
                (
                    uid,
                    chat_type,
                    key,
                    message.get("platform_id"),
                    message["created_at"],
                    json.dumps(value, ensure_ascii=False),
                ),
            )

    def counts(self, db, identifier):
        self.update(
            identifier,
            conversations=db.execute(
                "SELECT count(*) FROM contacts c WHERE EXISTS(SELECT 1 FROM transcript t "
                "WHERE t.uid=c.uid AND t.chat_type=c.chat_type AND t.included=1)"
            ).fetchone()[0],
            completed=db.execute(
                "SELECT count(*) FROM contacts c WHERE done=1 AND EXISTS(SELECT 1 FROM transcript t "
                "WHERE t.uid=c.uid AND t.chat_type=c.chat_type AND t.included=1)"
            ).fetchone()[0],
            messages=db.execute("SELECT count(*) FROM transcript WHERE included=1").fetchone()[0],
        )

    async def run(self, identifier):
        db = None
        try:
            db = self.connect(identifier)
            job = self.store.one("SELECT * FROM exports WHERE id=?", (identifier,))
            shop_id = job["shop_id"]
            # Platform history is merged with a stable copy of the locally saved transcript.
            if job["phase"] == "discover":
                for conv in self.store.all("SELECT * FROM conversations WHERE shop_id=?", (shop_id,)):
                    uid, chat_type = conv["buyer_uid"], conv["platform_chat_type"]
                    db.execute(
                        "INSERT OR IGNORE INTO contacts(uid,chat_type,name) VALUES(?,?,?)",
                        (uid, chat_type, conv["buyer_name"]),
                    )
                    self.add_messages(
                        db,
                        uid,
                        chat_type,
                        self.store.all("SELECT * FROM messages WHERE conversation_id=?", (conv["id"],)),
                        "local",
                    )
                db.commit()
                if job["scope"] == "platform":
                    page = job["page"]
                    while True:
                        response = await self.history.conversations(shop_id, page, 20)
                        for contact in response["items"]:
                            db.execute(
                                "INSERT OR IGNORE INTO contacts(uid,chat_type,name) VALUES(?,?,?)",
                                (contact["buyer_uid"], contact["chat_type"], contact["buyer_name"]),
                            )
                        db.commit()
                        self.counts(db, identifier)
                        if not response["has_more"]:
                            break
                        signature = hashlib.sha256(
                            json.dumps([(c["buyer_uid"], c["chat_type"]) for c in response["items"]]).encode()
                        ).hexdigest()
                        repeated = db.execute(
                            "SELECT page FROM discovered_pages WHERE signature=? AND page<>?",
                            (signature, page),
                        ).fetchone()
                        if not response["items"] or repeated:
                            raise ProtocolError("平台会话分页没有推进，已停止导出，避免重复循环。")
                        db.execute("INSERT OR REPLACE INTO discovered_pages VALUES(?,?)", (page, signature))
                        db.commit()
                        page += 1
                        self.update(identifier, page=page)
                        await asyncio.sleep(0.05)
                self.update(identifier, phase="messages")
            if job["scope"] == "platform":
                contacts = db.execute("SELECT * FROM contacts WHERE done=0 ORDER BY uid,chat_type").fetchall()
                for contact in contacts:
                    before = contact["cursor"]
                    seen = {before}
                    while True:
                        response = await self.history.messages(
                            shop_id, contact["uid"], before, 50, contact["chat_type"]
                        )
                        self.add_messages(
                            db, contact["uid"], contact["chat_type"], response["messages"], "platform"
                        )
                        if not response["has_more"]:
                            db.execute(
                                "UPDATE contacts SET done=1 WHERE uid=? AND chat_type=?",
                                (contact["uid"], contact["chat_type"]),
                            )
                        else:
                            cursor = response["next_cursor"]
                            if cursor in seen:
                                raise ProtocolError("平台消息分页重复，导出已停止，可稍后继续。")
                            seen.add(cursor)
                            db.execute(
                                "UPDATE contacts SET cursor=? WHERE uid=? AND chat_type=?",
                                (cursor, contact["uid"], contact["chat_type"]),
                            )
                            before = cursor
                        db.commit()
                        self.counts(db, identifier)
                        if not response["has_more"]:
                            break
                        await asyncio.sleep(0.05)
            else:
                db.execute("UPDATE contacts SET done=1")
                db.commit()
            recalls = db.execute(
                "SELECT uid,chat_type,payload FROM transcript WHERE json_extract(payload,'$.presentation.withdraw_target') IS NOT NULL"
            ).fetchall()
            for recall in recalls:
                notice = json.loads(recall["payload"])["presentation"]
                row = db.execute(
                    "SELECT key,payload FROM transcript WHERE uid=? AND chat_type=? AND platform_id=?",
                    (recall["uid"], recall["chat_type"], notice["withdraw_target"]),
                ).fetchone()
                if row:
                    value = json.loads(row["payload"])
                    value["content"] = notice.get("body", "消息已撤回")
                    value["presentation"] = {"version": 1, "kind": "withdrawn", "body": value["content"]}
                    db.execute(
                        "UPDATE transcript SET payload=? WHERE uid=? AND chat_type=? AND key=?",
                        (
                            json.dumps(value, ensure_ascii=False),
                            recall["uid"],
                            recall["chat_type"],
                            row["key"],
                        ),
                    )
            db.commit()
            baseline_path = self.snapshot_path(job["baseline_id"]) if job.get("baseline_id") else None
            new_count, changed_count = select_messages(db, job["mode"], baseline_path)
            self.update(identifier, new_messages=new_count, updated_messages=changed_count)
            self.counts(db, identifier)
            self.update(identifier, phase="package")
            packing = asyncio.create_task(
                asyncio.to_thread(
                    self.package, identifier, job, db_path=self.workspace(identifier) / "transcripts.sqlite3"
                )
            )
            try:
                await asyncio.shield(packing)
            except asyncio.CancelledError:
                await packing
                raise
            self.update(identifier, status="done", error="", phase="done")
        except asyncio.CancelledError:
            self.update(identifier, status="cancelled", error="导出已暂停，可继续任务。")
        except Exception as exc:
            self.update(
                identifier,
                status="failed",
                error=str(exc) if isinstance(exc, ProtocolError) else "导出未完成，请继续任务或新建导出。",
            )
        finally:
            if db is not None:
                db.close()
            self.tasks.pop(identifier, None)

    def package(self, identifier, job, db_path):
        shop = self.store.one("SELECT name,platform_shop_id FROM shops WHERE id=?", (job["shop_id"],))
        db = sqlite3.connect(db_path)
        db.row_factory = sqlite3.Row
        folder = self.workspace(identifier)
        archive = folder / "conversations.zip"
        temporary = folder / "conversations.zip.part"

        def esc(v):
            return html.escape(str(v or ""), quote=True)

        try:
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as output:
                contacts = db.execute(
                    "SELECT * FROM contacts c WHERE EXISTS(SELECT 1 FROM transcript t "
                    "WHERE t.uid=c.uid AND t.chat_type=c.chat_type AND t.included=1) ORDER BY uid,chat_type"
                ).fetchall()
                manifest = {
                    "shop": shop,
                    "scope": job["scope"],
                    "mode": job["mode"],
                    "baseline_id": job["baseline_id"],
                    "baseline_at": job["baseline_at"],
                    "first_export": not bool(job["baseline_id"]),
                    "selection": "新增消息及内容更新（含撤回）；按消息 ID 和指纹比对，不按消息时间截断。"
                    if job["mode"] == "incremental"
                    else "全部可获取的消息。",
                    "created_at": job["created_at"],
                    "conversations": len(contacts),
                    "messages": db.execute("SELECT count(*) FROM transcript WHERE included=1").fetchone()[0],
                    "new_messages": db.execute(
                        "SELECT count(*) FROM transcript WHERE included=1 AND export_change='new'"
                    ).fetchone()[0],
                    "updated_messages": db.execute(
                        "SELECT count(*) FROM transcript WHERE included=1 AND export_change='updated'"
                    ).fetchone()[0],
                    "formats": ["csv", "json", "html", "markdown"],
                    "note": "平台导出范围为当前账号可访问且枚举接口返回的历史，不代表平台全部留存。图片和视频保留在线地址，不打包媒体原文件。",
                }
                output.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
                index = [
                    "<!doctype html><meta charset='utf-8'><title>店铺聊天记录</title><h1>"
                    + esc(shop["name"])
                    + "</h1><p>打开各会话查看记录。媒体需联网访问；未包含登录态或模型密钥。</p><ul>"
                ]
                markdown_index = [
                    f"# {heading(shop['name'])} · 对话目录\n\n",
                    "打开对应会话阅读；图片、视频需联网访问。\n\n",
                ]
                combined_markdown = folder / "messages.md"
                csv_path = folder / "messages.csv"
                with (
                    csv_path.open("w", encoding="utf-8-sig", newline="") as file,
                    combined_markdown.open("w", encoding="utf-8") as combined,
                ):
                    title = "增量导出对话" if job["mode"] == "incremental" else "全部导出对话"
                    combined.write(f"# {heading(shop['name'])} · {title}\n\n")
                    if job["mode"] == "incremental":
                        combined.write("本文件仅含新增或更新记录；首次导出会包含全部可获取记录。\n\n")
                        combined.write(
                            f"新增 {manifest['new_messages']} 条，更新 {manifest['updated_messages']} 条。\n\n"
                        )
                    combined.write(
                        (
                            "范围：平台可获取的历史＋本地记录。"
                            if job["scope"] == "platform"
                            else "范围：仅本地保存的记录，不含平台历史。"
                        )
                        + "图片、视频保留在线链接。\n\n"
                    )
                    writer = csv.writer(file)
                    writer.writerow(
                        [
                            "买家昵称",
                            "买家ID",
                            "聊天类型",
                            "消息ID",
                            "时间（北京时间）",
                            "角色",
                            "内容",
                            "商品ID",
                            "媒体地址",
                            "来源",
                            "导出变更",
                        ]
                    )
                    for number, contact in enumerate(contacts, 1):
                        filename = f"conversations/{number:06d}"
                        index.append(
                            f'<li><a href="{filename}.html">{esc(contact["name"])} · {esc(contact["uid"])}</a></li>'
                        )
                        markdown_index.append(
                            f"- [{heading(contact['name'])} · {heading(contact['uid'])}]({filename}.md)\n"
                        )
                        markdown = [conversation_header(contact, shop["name"])]
                        transcript = []
                        lines = [
                            "<!doctype html><meta charset='utf-8'><meta name='referrer' content='no-referrer'><title>聊天记录</title><style>body{font:15px system-ui;max-width:900px;margin:30px auto;padding:12px}article{border-bottom:1px solid #ddd;padding:16px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere}img,video{max-width:420px;max-height:360px}small{color:#789}</style>",
                            f'<a href="../index.html">返回会话列表</a><h1>{esc(contact["name"])}</h1><p>买家 ID：{esc(contact["uid"])}</p>',
                        ]
                        rows = db.execute(
                            "SELECT * FROM transcript WHERE uid=? AND chat_type=? AND included=1 ORDER BY ts,length(key),key",
                            (contact["uid"], contact["chat_type"]),
                        )
                        for row in rows:
                            value = json.loads(row["payload"])
                            value["export_change"] = row["export_change"]
                            p = value.get("presentation") or {}
                            body = p.get("body", value["content"])
                            if p.get("kind") == "withdrawn":
                                value["content"] = body
                            transcript.append(value)
                            stamp = datetime.fromtimestamp(
                                value["created_at"], timezone(timedelta(hours=8), "Asia/Shanghai")
                            ).strftime("%Y-%m-%d %H:%M:%S")
                            markdown.append(message_markdown(value, stamp))
                            media = p.get("media") or []
                            product = p.get("product") or {}
                            cells = [
                                contact["name"],
                                contact["uid"],
                                contact["chat_type"],
                                value["platform_id"],
                                stamp,
                                value["role"],
                                value["content"],
                                product.get("id"),
                                " ".join(m["url"] for m in media),
                                value["source"],
                                {"updated": "更新", "new": "新增", "unchanged": "已有"}[row["export_change"]],
                            ]
                            # Avoid spreadsheet formulas when opening untrusted chat content as CSV.
                            writer.writerow(
                                [
                                    "'" + str(v)
                                    if str(v or "").lstrip().startswith(("=", "+", "-", "@"))
                                    else v
                                    for v in cells
                                ]
                            )
                            lines.append(
                                f"<article><small>{esc(stamp)} · {esc(value['role'])}</small><pre>{esc(body)}</pre>"
                            )
                            if product:
                                lines.append(
                                    f"<p>商品：{esc(product.get('name'))} · ID {esc(product.get('id'))} · {esc(product.get('price'))}</p>"
                                )
                            if p.get("order"):
                                lines.append(
                                    "<pre>" + esc(json.dumps(p["order"], ensure_ascii=False)) + "</pre>"
                                )
                            for item in media:
                                from .message_content import media_url, safe_url

                                url = safe_url(item.get("url"))
                                if media_url(url) and item.get("kind") in {"image", "emotion"}:
                                    lines.append(
                                        f'<img loading="lazy" referrerpolicy="no-referrer" src="{esc(url)}" alt="聊天图片">'
                                    )
                                elif media_url(url) and item.get("kind") == "video":
                                    lines.append(f'<video controls preload="none" src="{esc(url)}"></video>')
                                if url:
                                    lines.append(
                                        f'<p><a href="{esc(url)}" rel="noopener noreferrer">查看媒体原文件</a></p>'
                                    )
                            lines.append("</article>")
                        output.writestr(
                            filename + ".json",
                            json.dumps(
                                {
                                    "buyer_uid": contact["uid"],
                                    "buyer_name": contact["name"],
                                    "chat_type": contact["chat_type"],
                                    "messages": transcript,
                                },
                                ensure_ascii=False,
                                indent=2,
                            ),
                        )
                        output.writestr(filename + ".html", "".join(lines))
                        output.writestr(filename + ".md", "".join(markdown))
                        combined.write("".join(markdown) + "\n")
                output.write(csv_path, "messages.csv")
                output.write(combined_markdown, "messages.md")
                output.writestr("index.md", "".join(markdown_index))
                output.writestr("index.html", "".join(index) + "</ul>")
                output.writestr(
                    "README.txt",
                    "打开 index.html 或 index.md 浏览；messages.md 为全部对话；conversations/*.md 为单会话 Markdown；messages.csv 可用 Excel 打开；conversations/*.json 保留结构化消息。\n"
                    + manifest["selection"]
                    + "\n"
                    + manifest["note"],
                )
            temporary.replace(archive)
        finally:
            db.close()

    async def cancel(self, identifier):
        task = self.tasks.get(identifier)
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        job = self.store.one("SELECT status FROM exports WHERE id=?", (identifier,))
        if job and job["status"] == "running":
            self.update(identifier, status="cancelled", error="导出已暂停，可继续任务。")

    async def shutdown(self):
        for identifier in list(self.tasks):
            await self.cancel(identifier)
