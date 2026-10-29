"""Per-shop export snapshots: compare stable message identities, including late arrivals."""

import hashlib
import json
import sqlite3
from contextlib import closing

from .pdd_protocol import ProtocolError


def fingerprint(payload):
    value = json.loads(payload)
    # A local/platform source label is provenance, not a change to a message.
    value.pop("source", None)
    value.pop("export_change", None)
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def select_messages(db, mode, baseline_path):
    """Build a cumulative identity ledger, but include only this export's selected rows.

    The ledger is private to the job. It becomes a baseline only when packaging
    succeeds and the main exports row is marked done. Never infer delivery from ts.
    """
    db.execute(
        "CREATE TABLE IF NOT EXISTS delivered ("
        "uid TEXT NOT NULL, chat_type TEXT NOT NULL, key TEXT NOT NULL, fingerprint TEXT NOT NULL,"
        "PRIMARY KEY(uid,chat_type,key))"
    )
    db.execute("DELETE FROM delivered")
    if baseline_path and baseline_path.is_file():
        with closing(sqlite3.connect(baseline_path.resolve().as_uri() + "?mode=ro", uri=True)) as previous:
            has_ledger = previous.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='delivered'"
            ).fetchone()
            if has_ledger:
                cursor = previous.execute("SELECT uid,chat_type,key,fingerprint FROM delivered")
            else:
                # Existing successful full exports remain usable after upgrading.
                cursor = previous.execute("SELECT uid,chat_type,key,payload FROM transcript")
            while rows := cursor.fetchmany(500):
                db.executemany(
                    "INSERT INTO delivered VALUES(?,?,?,?)",
                    rows
                    if has_ledger
                    else [(uid, kind, key, fingerprint(payload)) for uid, kind, key, payload in rows],
                )
    elif baseline_path and mode == "incremental":
        raise ProtocolError("上次成功导出的基准文件已丢失，请先全量导出以重建基准。")

    new_count = changed_count = 0
    cursor = db.execute("SELECT uid,chat_type,key,payload FROM transcript")
    while rows := cursor.fetchmany(500):
        for uid, kind, key, payload in rows:
            digest = fingerprint(payload)
            previous = db.execute(
                "SELECT fingerprint FROM delivered WHERE uid=? AND chat_type=? AND key=?", (uid, kind, key)
            ).fetchone()
            change = "new" if not previous else "updated" if previous[0] != digest else "unchanged"
            included = mode == "full" or not previous or previous[0] != digest
            db.execute(
                "UPDATE transcript SET included=?,export_change=? WHERE uid=? AND chat_type=? AND key=?",
                (int(included), change, uid, kind, key),
            )
            if included:
                new_count += change == "new"
                changed_count += change == "updated"
            db.execute(
                "INSERT INTO delivered VALUES(?,?,?,?) ON CONFLICT(uid,chat_type,key) "
                "DO UPDATE SET fingerprint=excluded.fingerprint",
                (uid, kind, key, digest),
            )
    db.commit()
    return new_count, changed_count
