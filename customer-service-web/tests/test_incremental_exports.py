import io
import json
import sqlite3
import zipfile

from desk.store import Store
from test_shop_management import H, add_local, managed as managed, wait_job


def export(client, mode="incremental", scope="platform", shop_id=1):
    response = client.post(f"/api/shops/{shop_id}/exports", headers=H, json={"scope": scope, "mode": mode})
    assert response.status_code == 200, response.text
    if shop_id == 1:
        return wait_job(client, response.json())
    import time

    for _ in range(200):
        job = client.get(f"/api/shops/{shop_id}/exports/{response.json()['id']}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.015)
    raise AssertionError("Export did not finish")


def archive_data(client, job):
    response = client.get(f"/api/shops/{job['shop_id']}/exports/{job['id']}/download")
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        messages = [
            (row["buyer_uid"], m)
            for name in archive.namelist()
            if name.startswith("conversations/") and name.endswith(".json")
            for row in [json.loads(archive.read(name))]
            for m in row["messages"]
        ]
        return manifest, messages, archive.read("messages.md").decode("utf-8")


def add_message(app, conversation_id, identifier, content, stamp=200):
    return app.state.store.execute(
        "INSERT INTO messages(conversation_id,role,content,platform_id,created_at) VALUES(?,'customer',?,?,?)",
        (conversation_id, content, identifier, stamp),
    )


def test_first_incremental_is_full_then_existing_conversation_new_messages_are_exported(managed):
    client, app, _, _ = managed
    cid = add_local(app, platform_id="local-original")
    first = export(client, scope="local")
    assert first["status"] == "done" and first["messages"] == first["new_messages"] == 1
    assert first["baseline_id"] is None
    add_message(app, cid, "new-same-time", "same time as original", stamp=200)
    add_message(app, cid, "late-older", "late arriving old message", stamp=100)
    second = export(client, scope="local")
    manifest, messages, _ = archive_data(client, second)
    assert second["messages"] == second["new_messages"] == 2
    assert manifest["baseline_id"] == first["id"] and manifest["mode"] == "incremental"
    assert {m["platform_id"] for _, m in messages} == {"new-same-time", "late-older"}
    assert export(client, scope="local")["messages"] == 0


def test_platform_incremental_uses_existing_full_snapshot_and_exports_only_new_items(managed):
    client, app, _, _ = managed
    baseline = export(client, mode="full")
    # Simulate a pre-upgrade successful export which has no identity ledger.
    with sqlite3.connect(app.state.exports.snapshot_path(baseline["id"])) as db:
        db.execute("DROP TABLE delivered")
    cid = add_local(app, uid="new-conversation", platform_id="local-new")
    add_message(app, cid, "late", "late arrival", stamp=50)
    job = export(client)
    assert job["status"] == "done", job
    manifest, messages, _ = archive_data(client, job)
    assert job["messages"] == 4 and manifest["baseline_id"] == baseline["id"]
    assert {uid for uid, _ in messages} == {"new-conversation"}
    assert export(client)["messages"] == 0


def test_empty_incremental_is_valid_download_and_keeps_cumulative_baseline(managed):
    client, app, _, _ = managed
    cid = add_local(app, platform_id="original")
    full = export(client, mode="full", scope="local")
    empty = export(client, scope="local")
    assert empty["status"] == "done" and empty["messages"] == empty["conversations"] == 0
    manifest, messages, markdown = archive_data(client, empty)
    assert messages == [] and "新增 0 条" in markdown
    assert manifest["baseline_id"] == full["id"]
    add_message(app, cid, "next", "new after empty")
    job = export(client, scope="local")
    assert job["baseline_id"] == empty["id"] and job["messages"] == 1
    # Even if a platform-retained message temporarily disappears and reappears,
    # its delivered identity remains in the cumulative ledger.
    app.state.store.execute("DELETE FROM messages WHERE platform_id='original'")
    export(client, scope="local")
    add_message(app, cid, "original", "Local content")
    assert export(client, scope="local")["messages"] == 0


def test_updated_and_recalled_messages_are_marked_without_reexporting_unchanged_rows(managed):
    client, app, _, _ = managed
    cid = add_local(app, platform_id="original")
    add_message(app, cid, "unchanged", "keep original")
    export(client, mode="full", scope="local")
    app.state.store.execute(
        "UPDATE messages SET content='消息已撤回',presentation=? WHERE platform_id='original'",
        (json.dumps({"kind": "withdrawn", "body": "消息已撤回"}),),
    )
    job = export(client, scope="local")
    assert job["messages"] == job["updated_messages"] == 1 and job["new_messages"] == 0
    _, messages, markdown = archive_data(client, job)
    assert messages[0][1]["platform_id"] == "original" and messages[0][1]["export_change"] == "updated"
    assert "消息已撤回" in markdown and "变更：更新" in markdown and "Local content" not in markdown
    assert export(client, scope="local")["messages"] == 0


def test_failed_package_does_not_advance_baseline_and_can_resume(managed, monkeypatch):
    client, app, _, _ = managed
    cid = add_local(app, platform_id="base")
    baseline = export(client, mode="full", scope="local")
    add_message(app, cid, "after-failure", "not yet delivered")
    original = app.state.exports.package

    def broken(*args, **kwargs):
        raise OSError("simulated full disk")

    monkeypatch.setattr(app.state.exports, "package", broken)
    failed = export(client, scope="local")
    assert failed["status"] == "failed"
    assert app.state.exports.last_success(1, "local")["id"] == baseline["id"]
    monkeypatch.setattr(app.state.exports, "package", original)
    assert client.post(f"/api/shops/1/exports/{failed['id']}/resume", headers=H).status_code == 200
    completed = wait_job(client, failed)
    assert completed["status"] == "done" and completed["messages"] == 1
    assert export(client, scope="local")["messages"] == 0


def test_platform_fetch_failure_preserves_previous_success(managed):
    client, app, _, behavior = managed
    baseline = export(client, mode="full")
    behavior["fail"] = True
    failed = export(client)
    assert failed["status"] == "failed"
    assert app.state.exports.last_success(1, "platform")["id"] == baseline["id"]
    behavior["fail"] = False
    assert client.post(f"/api/shops/1/exports/{failed['id']}/resume", headers=H).status_code == 200
    assert wait_job(client, failed)["messages"] == 0


def test_shop_and_scope_baselines_are_isolated_and_persist_across_store_reopen(managed):
    client, app, calls, _ = managed
    add_local(app, platform_id="local-only")
    local = export(client, mode="full", scope="local")
    platform = export(client)
    assert platform["baseline_id"] is None and platform["messages"] == 5
    assert export(client, scope="local")["messages"] == 0
    second = client.post("/api/shops", headers=H, json={"name": "Second"}).json()["id"]
    other = export(client, scope="local", shop_id=second)
    assert other["status"] == "done" and other["baseline_id"] is None
    reopened = Store(app.state.store.directory)
    assert (
        reopened.one("SELECT mode,baseline_id FROM exports WHERE id=?", (platform["id"],))["mode"]
        == "incremental"
    )
    shops = client.get("/api/shops").json()
    baseline = next(s for s in shops if s["id"] == 1)["export_baselines"]
    assert baseline["platform"]["id"] == platform["id"]
    assert baseline["local"]["id"] != local["id"]
    assert not next(s for s in shops if s["id"] == second)["export_baselines"].get("platform")
    assert calls


def test_cannot_resume_a_stale_failed_job_after_a_newer_success(managed, monkeypatch):
    client, app, _, _ = managed
    original = app.state.exports.package
    monkeypatch.setattr(app.state.exports, "package", lambda *a, **kw: (_ for _ in ()).throw(OSError()))
    failed = export(client, scope="local")
    monkeypatch.setattr(app.state.exports, "package", original)
    assert export(client, scope="local")["status"] == "done"
    assert client.post(f"/api/shops/1/exports/{failed['id']}/resume", headers=H).status_code == 409


def test_missing_baseline_requires_full_rebuild_instead_of_silent_duplicates(managed):
    client, app, _, _ = managed
    first = export(client, scope="local")
    app.state.exports.snapshot_path(first["id"]).rename(
        app.state.exports.snapshot_path(first["id"]).with_suffix(".bak")
    )
    response = client.post("/api/shops/1/exports", headers=H, json={"scope": "local", "mode": "incremental"})
    assert response.status_code == 409 and "基准" in response.json()["detail"]
    assert export(client, mode="full", scope="local")["status"] == "done"


def test_invalid_mode_is_rejected(managed):
    client, _, _, _ = managed
    assert client.post("/api/shops/1/exports", headers=H, json={"mode": "yesterday"}).status_code == 422


def test_pause_during_packaging_does_not_mark_the_export_successful(managed, monkeypatch):
    import threading

    client, app, _, _ = managed
    add_local(app, platform_id="before-pause")
    baseline = export(client, mode="full", scope="local")
    entered, release = threading.Event(), threading.Event()
    original = app.state.exports.package

    def slow_package(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(app.state.exports, "package", slow_package)
    pending = client.post(
        "/api/shops/1/exports", headers=H, json={"scope": "local", "mode": "incremental"}
    ).json()
    assert entered.wait(3)
    # cancel() waits for the worker to finish safely, but must not commit a baseline.
    future = client.portal.start_task_soon(app.state.exports.cancel, pending["id"])
    try:
        assert client.portal.call(lambda: app.state.exports.tasks[pending["id"]].cancelling()) > 0
    finally:
        release.set()
    future.result(timeout=5)
    job = wait_job(client, pending)
    assert job["status"] == "cancelled"
    assert app.state.exports.last_success(1, "local")["id"] == baseline["id"]


def test_incremental_progress_survives_export_service_restart(managed):
    from desk.exports import Exports

    client, app, _, _ = managed
    cid = add_local(app, platform_id="old")
    baseline = export(client, mode="full", scope="local")
    add_message(app, cid, "after-restart", "new message")
    restarted = Exports(Store(app.state.store.directory), app.state.exports.history)
    pending = client.portal.call(restarted.start, 1, "local", "incremental")
    completed = wait_job(client, pending)
    assert completed["status"] == "done" and completed["baseline_id"] == baseline["id"]
    assert completed["messages"] == 1
