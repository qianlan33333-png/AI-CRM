from __future__ import annotations

MOJIBAKE_MARKERS = frozenset("\u00c2\u00c3\u00e2\u00e6\u00e5\u00e7\u00e8\u00e9\u00f0\u0178\u0152")


def _suspicious_score(text: str) -> int:
    return sum(1 for char in text if char in MOJIBAKE_MARKERS or char == "\ufffd")


def _has_repaired_signal(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text) or any(ord(char) > 0xFFFF for char in text)


def repair_utf8_mojibake(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    original_score = _suspicious_score(text)
    for source_encoding in ("latin1", "cp1252"):
        try:
            repaired = text.encode(source_encoding).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if repaired == text:
            continue
        repaired_score = _suspicious_score(repaired)
        if original_score and (repaired_score < original_score or _has_repaired_signal(repaired)):
            return repaired
    return text
