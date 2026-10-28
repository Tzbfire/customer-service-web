"""Compare browser-observed public PDD assets with this adapter; never reads credentials.

This is source compatibility evidence, not a successful authenticated network test.
"""

import argparse
import hashlib
import json
import re
from pathlib import Path


def inspect(root):
    folder = root / "evidence" / "protocol-2026-10-28"
    manifest = json.loads((folder / "live-assets.json").read_text(encoding="utf-8"))
    record = next(item for item in manifest["assets"] if item["file"] == "live-index.js")
    asset = (folder / record["file"]).read_bytes()
    if hashlib.sha256(asset).hexdigest() != record["sha256"]:
        raise ValueError("The captured source does not match its recorded SHA-256.")
    text = asset.decode("utf-8")
    build = re.search(r"window\.SERVICE_BUILD_TIME\s*=\s*(\d+)", text)
    adapter = (root / "desk" / "channels.py").read_text(encoding="utf-8")
    protocol_path = root / "desk" / "pdd_protocol.py"
    protocol = protocol_path.read_text(encoding="utf-8") if protocol_path.exists() else ""
    dynamic = "discover_build" in protocol and "self.protocol.open(cookies)" in adapter
    probe_path = folder / "updated-adapter-probe.json"
    live_probe = json.loads(probe_path.read_text(encoding="utf-8")) if probe_path.exists() else {}
    receiver_path = folder / "receiver-integration-check.json"
    receiver_probe = json.loads(receiver_path.read_text(encoding="utf-8")) if receiver_path.exists() else {}
    pinned = re.search(r"[\"\']version[\"\']\s*:\s*[\"\'](\d{12,14})[\"\']", adapter)
    send_start = text.index('cmd:"send_message",anti_content:')
    send_excerpt = text[send_start : send_start + 650]
    wire_fields = ["anti_content", "ts", "msg_id", "chat_type", "status", "is_read", "hash", "random"]
    # The local send method is deliberately inspected apart from unrelated message parsing.
    outgoing = adapter[adapter.index("    async def send(") : adapter.index("    async def login(")]
    local_fields = set(re.findall(r"[\"\'](\w+)[\"\']\s*:", outgoing))
    if 'data["data"]["message"]["chat_type"] =' in outgoing:
        local_fields.add("chat_type")
    fields_in_current = [field for field in wire_fields if re.search(r"\b" + field + r":", send_excerpt)]
    return {
        "evidence_kind": "browser-observed assets, downloaded and hash-verified",
        "captured_at": manifest["captured_at"],
        "frontend_build": build.group(1) if build else None,
        "adapter_pinned_build": pinned.group(1) if pinned else None,
        "adapter_version_strategy": "discover_from_authenticated_workstation" if dynamic else "fixed_build",
        "live_adapter_build": live_probe.get("build"),
        "live_auth_passed": live_probe.get("authenticated") is True,
        "live_heartbeat_passed": live_probe.get("heartbeat") == "ok",
        "live_sync_passed": receiver_probe.get("sequence_sync") == "ok",
        "live_sequence_sync_implemented": "sync_once" in protocol and "pdd_sync" in protocol,
        "legacy_token_path_still_in_current_client": "/getToken" in text and ".chats({version:3}" in text,
        "legacy_uses_current_build": 'version="+(window.SERVICE_BUILD_TIME||0)' in text,
        "new_imsdk_present": "/plateau/sync/get_init_state" in text and "getAuthToken(17)" in text,
        "new_subsystem_auth_endpoint": "/janus/api/subSystem/getAuthToken",
        "new_titan_binary_transport_present": 'e.binaryType="arraybuffer"' in text
        and "sessionResumptionReq" in text,
        "send_endpoint_still_present": "/plateau/chat/send_message" in text,
        "current_sender_fields": fields_in_current,
        "fields_not_in_local_sender": [field for field in fields_in_current if field not in local_fields],
        "compatibility_conclusion": "live legacy JSON auth, heartbeat and sequence recovery verified; buyer delivery and outgoing acceptance not yet observed",
        "not_verified": [
            "real buyer-message arrival",
            "real outgoing message acceptance",
            "Titan binary transport",
        ],
        "source_url": record["url"],
        "source_sha256": record["sha256"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--require-build-match",
        action="store_true",
        help="Exit 1 if this adapter pins a different build from the captured client.",
    )
    args = parser.parse_args()
    report = inspect(Path(__file__).resolve().parents[1])
    rendered = json.dumps(report, ensure_ascii=True, indent=2)
    print(rendered)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    if args.require_build_match:
        actual = (
            report["live_adapter_build"]
            if report["adapter_version_strategy"] == "discover_from_authenticated_workstation"
            else report["adapter_pinned_build"]
        )
        if actual != report["frontend_build"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
