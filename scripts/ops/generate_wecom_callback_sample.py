#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
from typing import Any
from urllib.parse import urlencode
import xml.etree.ElementTree as ET

try:
    from scripts.script_runtime import ensure_repo_root_on_path, print_json
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from script_runtime import ensure_repo_root_on_path, print_json

ensure_repo_root_on_path()

from aicrm_next.channels.channel_entry.inbox import wecom_callback_idempotency_key
from aicrm_next.channels.channel_entry.wecom_crypto import compute_signature, encrypt_message


DEFAULT_CALLBACK_BASE_URL = "http://127.0.0.1:5002/wecom/external-contact/callback"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _load_env_file(path: str) -> bool:
    env_path = Path(path)
    if not env_path.exists():
        return False
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")
    return True


def _required_env() -> dict[str, str]:
    values = {
        "corp_id": _text(os.getenv("WECOM_CORP_ID")),
        "token": _text(os.getenv("WECOM_CALLBACK_TOKEN")),
        "aes_key": _text(os.getenv("WECOM_CALLBACK_AES_KEY")),
    }
    missing = [key for key, value in values.items() if not value]
    if missing:
        raise SystemExit("missing required callback env: " + ", ".join(missing))
    return values


def build_plain_event(args: argparse.Namespace, *, corp_id: str, create_time: str) -> dict[str, str]:
    return {
        "ToUserName": corp_id,
        "Event": _text(args.event) or "change_external_contact",
        "ChangeType": _text(args.change_type) or "del_external_contact",
        "ExternalUserID": _text(args.external_userid) or "wm_codex_canary",
        "UserID": _text(args.user_id) or "codex-canary-user",
        "CreateTime": create_time,
        "WelcomeCode": _text(args.welcome_code),
        "State": _text(args.state) or f"codex_callback_canary_{create_time}",
    }


def event_to_xml(event: dict[str, str]) -> str:
    root = ET.Element("xml")
    for tag, value in event.items():
        node = ET.SubElement(root, tag)
        node.text = value
    return ET.tostring(root, encoding="utf-8").decode("utf-8")


def build_sample(args: argparse.Namespace) -> dict[str, Any]:
    env_loaded = _load_env_file(str(args.env_file)) if _text(args.env_file) else False
    config = _required_env()
    timestamp = _text(args.timestamp) or str(int(time.time()))
    nonce = _text(args.nonce) or f"codex-canary-{timestamp}"
    event = build_plain_event(args, corp_id=config["corp_id"], create_time=timestamp)
    plain_xml = event_to_xml(event)
    encrypted = encrypt_message(plain_xml, config["aes_key"], config["corp_id"])
    signature = compute_signature(config["token"], timestamp, nonce, encrypted)
    body = f"<xml><Encrypt>{encrypted}</Encrypt></xml>"
    query = urlencode({"timestamp": timestamp, "nonce": nonce, "msg_signature": signature})
    callback_url = f"{_text(args.callback_base_url) or DEFAULT_CALLBACK_BASE_URL}?{query}"
    idempotency_key = wecom_callback_idempotency_key(config["corp_id"], event)

    if _text(args.body_file):
        Path(args.body_file).write_text(body, encoding="utf-8")
    if _text(args.url_file):
        Path(args.url_file).write_text(callback_url + "\n", encoding="utf-8")
    metadata = {
        "ok": True,
        "env_file_loaded": env_loaded,
        "callback_url": callback_url,
        "body_file": _text(args.body_file),
        "url_file": _text(args.url_file),
        "idempotency_key": idempotency_key,
        "event_summary": {
            "ToUserName": event["ToUserName"],
            "Event": event["Event"],
            "ChangeType": event["ChangeType"],
            "ExternalUserID_present": bool(event["ExternalUserID"]),
            "UserID_present": bool(event["UserID"]),
            "WelcomeCode_present": bool(event["WelcomeCode"]),
            "State_present": bool(event["State"]),
        },
        "plain_xml_bytes": len(plain_xml.encode("utf-8")),
        "encrypted_body_bytes": len(body.encode("utf-8")),
        "notes": [
            "Generated from current callback env without printing token or AES key.",
            "Default ChangeType is del_external_contact so the inbox worker records the canary without planning channel-entry effects.",
            "Use only in an approved staging or production callback canary.",
        ],
    }
    if _text(args.metadata_file):
        Path(args.metadata_file).write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if bool(args.print_body):
        metadata["callback_body"] = body
    return metadata


def run(argv: list[str] | None = None) -> dict[str, Any]:
    return build_sample(_parse_args(argv))


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a valid encrypted WeCom callback canary sample from current callback env.")
    parser.add_argument("--env-file", default="")
    parser.add_argument("--callback-base-url", default=DEFAULT_CALLBACK_BASE_URL)
    parser.add_argument("--body-file", default="")
    parser.add_argument("--url-file", default="")
    parser.add_argument("--metadata-file", default="")
    parser.add_argument("--timestamp", default="")
    parser.add_argument("--nonce", default="")
    parser.add_argument("--event", default="change_external_contact")
    parser.add_argument("--change-type", default="del_external_contact")
    parser.add_argument("--external-userid", default="wm_codex_canary")
    parser.add_argument("--user-id", default="codex-canary-user")
    parser.add_argument("--welcome-code", default="")
    parser.add_argument("--state", default="")
    parser.add_argument("--print-body", action="store_true", default=False)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    payload = run(argv)
    print_json(payload, indent=2)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
