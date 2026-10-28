from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from time import time

from cryptography.fernet import Fernet

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE, password TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('admin','agent')), active INTEGER NOT NULL DEFAULT 1,
 created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
 token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS shops (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, platform_shop_id TEXT,
 platform_user_id TEXT, cookies TEXT, mode TEXT NOT NULL DEFAULT 'review', created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
 id TEXT PRIMARY KEY, shop_id INTEGER NOT NULL REFERENCES shops(id),
 title TEXT NOT NULL, goods_id TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT 'product',
 filename TEXT NOT NULL, sha256 TEXT NOT NULL, content TEXT NOT NULL DEFAULT '',
 state TEXT NOT NULL DEFAULT 'parsing', error TEXT NOT NULL DEFAULT '',
 created_by INTEGER NOT NULL REFERENCES users(id), created_at REAL NOT NULL,
 UNIQUE(shop_id,sha256,goods_id)
);
CREATE TABLE IF NOT EXISTS chunks (
 id INTEGER PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
 position INTEGER NOT NULL, content TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_document ON chunks(document_id);
CREATE TABLE IF NOT EXISTS conversations (
 id INTEGER PRIMARY KEY, shop_id INTEGER NOT NULL REFERENCES shops(id), buyer_uid TEXT NOT NULL,
 buyer_name TEXT NOT NULL, goods_id TEXT NOT NULL DEFAULT '', mode TEXT NOT NULL DEFAULT 'ai',
 assignee INTEGER REFERENCES users(id), needs_human INTEGER NOT NULL DEFAULT 0,
 updated_at REAL NOT NULL, UNIQUE(shop_id,buyer_uid)
);
CREATE TABLE IF NOT EXISTS messages (
 id INTEGER PRIMARY KEY, conversation_id INTEGER NOT NULL REFERENCES conversations(id),
 role TEXT NOT NULL, content TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'received',
 sources TEXT NOT NULL DEFAULT '[]', platform_id TEXT, request_key TEXT UNIQUE,
 created_by INTEGER REFERENCES users(id), created_at REAL NOT NULL,
 UNIQUE(conversation_id,platform_id)
);
CREATE INDEX IF NOT EXISTS messages_conversation ON messages(conversation_id,id);
CREATE TABLE IF NOT EXISTS buyer_profiles (
 shop_id INTEGER NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
 buyer_uid TEXT NOT NULL, chat_type TEXT NOT NULL DEFAULT '', nickname TEXT NOT NULL DEFAULT '',
 next_check REAL NOT NULL, PRIMARY KEY(shop_id,buyer_uid,chat_type)
);
CREATE TABLE IF NOT EXISTS pdd_sync (
 shop_id INTEGER NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
 seq_type INTEGER NOT NULL, seq_id INTEGER NOT NULL, updated_at REAL NOT NULL,
 PRIMARY KEY(shop_id,seq_type)
);
CREATE TABLE IF NOT EXISTS audit (
 id INTEGER PRIMARY KEY, user_id INTEGER, action TEXT NOT NULL, detail TEXT NOT NULL,
 created_at REAL NOT NULL
);
"""


class Store:
    def __init__(self, directory: Path):
        self.directory = directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / "uploads").mkdir(exist_ok=True)
        key_path = self.directory / "secret.key"
        try:
            with key_path.open("xb") as file:
                file.write(Fernet.generate_key())
            os.chmod(key_path, 0o600)
        except FileExistsError:
            pass
        self.cipher = Fernet(key_path.read_bytes())
        self.path = self.directory / "desk.sqlite3"
        with self.connect() as db:
            db.executescript(SCHEMA)
            columns = {row[1] for row in db.execute("PRAGMA table_info(conversations)")}
            for name in ("platform_conv_id", "platform_chat_type"):
                if name not in columns:
                    db.execute(f"ALTER TABLE conversations ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
            message_columns = {row[1] for row in db.execute("PRAGMA table_info(messages)")}
            if "presentation" not in message_columns:
                db.execute("ALTER TABLE messages ADD COLUMN presentation TEXT NOT NULL DEFAULT '{}'")

    def recover_interrupted(self):
        with self.connect() as db:
            db.execute(
                "UPDATE documents SET state='failed', error=? WHERE state='parsing'",
                ("服务重启中断了解析，请重试。",),
            )
            db.execute("UPDATE messages SET state='uncertain' WHERE state='sending'")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def all(self, sql, params=()):
        with self.connect() as db:
            return [dict(row) for row in db.execute(sql, params).fetchall()]

    def one(self, sql, params=()):
        with self.connect() as db:
            row = db.execute(sql, params).fetchone()
            return dict(row) if row else None

    def execute(self, sql, params=()):
        with self.connect() as db:
            return db.execute(sql, params).lastrowid

    def encrypt(self, text):
        return self.cipher.encrypt(text.encode()).decode() if text else ""

    def decrypt(self, text):
        return self.cipher.decrypt(text.encode()).decode() if text else ""

    def model(self):
        row = self.one("SELECT value FROM settings WHERE key='model'")
        value = json.loads(row["value"]) if row else {}
        value["api_key"] = self.decrypt(value.get("api_key", ""))
        return value

    def save_model(self, value):
        value = dict(value)
        value["api_key"] = self.encrypt(value.get("api_key", ""))
        self.execute(
            "INSERT INTO settings(key,value) VALUES('model',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (json.dumps(value, ensure_ascii=False),),
        )

    def audit(self, user_id, action, detail=""):
        self.execute(
            "INSERT INTO audit(user_id,action,detail,created_at) VALUES(?,?,?,?)",
            (user_id, action, detail, time()),
        )


def password_hash(password):
    salt = secrets.token_bytes(16)
    value = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 400_000)
    return f"{salt.hex()}:{value.hex()}"


def password_matches(password, encoded):
    salt, expected = encoded.split(":")
    value = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 400_000)
    return hmac.compare_digest(value.hex(), expected)


def session_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()
