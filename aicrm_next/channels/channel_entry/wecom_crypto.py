from __future__ import annotations

import base64
import hashlib
import hmac
import os
import socket
import struct
import time
import xml.etree.ElementTree as ET

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

BLOCK_SIZE = 32
CALLBACK_MAX_AGE_SECONDS = 300
CALLBACK_MAX_FUTURE_SKEW_SECONDS = 60


class WeComCallbackError(RuntimeError):
    pass


def _aes_key_bytes(aes_key: str) -> bytes:
    if not aes_key:
        raise WeComCallbackError("WECOM_CALLBACK_AES_KEY is not configured")
    return base64.b64decode(f"{aes_key}=")


def _pkcs7_unpad(data: bytes) -> bytes:
    pad_len = data[-1]
    if pad_len < 1 or pad_len > BLOCK_SIZE:
        raise WeComCallbackError("invalid PKCS7 padding")
    return data[:-pad_len]


def _pkcs7_pad(data: bytes) -> bytes:
    pad_len = BLOCK_SIZE - (len(data) % BLOCK_SIZE)
    if pad_len == 0:
        pad_len = BLOCK_SIZE
    return data + bytes([pad_len]) * pad_len


def compute_signature(token: str, timestamp: str, nonce: str, encrypted: str) -> str:
    return hashlib.sha1("".join(sorted([token, timestamp, nonce, encrypted])).encode("utf-8")).hexdigest()


def verify_signature(token: str, timestamp: str, nonce: str, encrypted: str, signature: str) -> None:
    expected = compute_signature(token, timestamp, nonce, encrypted)
    if not hmac.compare_digest(expected, str(signature or "")):
        raise WeComCallbackError("invalid callback signature")


def validate_callback_timestamp(
    timestamp: str,
    *,
    now: float | None = None,
    max_age_seconds: int = CALLBACK_MAX_AGE_SECONDS,
    max_future_skew_seconds: int = CALLBACK_MAX_FUTURE_SKEW_SECONDS,
) -> None:
    try:
        callback_time = int(str(timestamp or "").strip())
    except (TypeError, ValueError) as exc:
        raise WeComCallbackError("invalid callback timestamp") from exc
    current_time = int(time.time() if now is None else now)
    if callback_time < current_time - max(1, int(max_age_seconds)):
        raise WeComCallbackError("expired callback timestamp")
    if callback_time > current_time + max(0, int(max_future_skew_seconds)):
        raise WeComCallbackError("callback timestamp is too far in the future")


def decrypt_message(encrypted: str, aes_key: str, corp_id: str) -> str:
    key = _aes_key_bytes(aes_key)
    cipher = Cipher(algorithms.AES(key), modes.CBC(key[:16]))
    decryptor = cipher.decryptor()
    decrypted = decryptor.update(base64.b64decode(encrypted)) + decryptor.finalize()
    plain = _pkcs7_unpad(decrypted)
    xml_length = socket.ntohl(struct.unpack("I", plain[16:20])[0])
    xml_bytes = plain[20 : 20 + xml_length]
    received_corp_id = plain[20 + xml_length :].decode("utf-8")
    if corp_id and received_corp_id != corp_id:
        raise WeComCallbackError("callback corp_id mismatch")
    return xml_bytes.decode("utf-8")


def encrypt_message(message: str, aes_key: str, corp_id: str) -> str:
    key = _aes_key_bytes(aes_key)
    payload = os.urandom(16) + struct.pack("I", socket.htonl(len(message.encode("utf-8")))) + message.encode("utf-8") + corp_id.encode("utf-8")
    cipher = Cipher(algorithms.AES(key), modes.CBC(key[:16]))
    encryptor = cipher.encryptor()
    encrypted = encryptor.update(_pkcs7_pad(payload)) + encryptor.finalize()
    return base64.b64encode(encrypted).decode("utf-8")


def parse_callback_xml(xml_text: str) -> dict[str, str]:
    root = ET.fromstring(xml_text)
    return {child.tag: (child.text or "") for child in root}


def build_encrypted_reply(message: str, token: str, aes_key: str, corp_id: str, nonce: str = "") -> str:
    timestamp = str(int(time.time()))
    reply_nonce = nonce or os.urandom(8).hex()
    encrypted = encrypt_message(message, aes_key, corp_id)
    signature = compute_signature(token, timestamp, reply_nonce, encrypted)
    root = ET.Element("xml")
    for tag, value in {"Encrypt": encrypted, "MsgSignature": signature, "TimeStamp": timestamp, "Nonce": reply_nonce}.items():
        node = ET.SubElement(root, tag)
        node.text = value
    return ET.tostring(root, encoding="utf-8").decode("utf-8")
