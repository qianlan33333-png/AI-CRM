from __future__ import annotations

import argparse
import json
import socket
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Submit a safe aggregate result to the local operation runner.")
    parser.add_argument("--socket", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--result-file", required=True)
    args = parser.parse_args()
    result = json.loads(Path(args.result_file).read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise SystemExit("result JSON must be an object")
    message = {
        "request_id": args.request_id,
        "event_type": "completed",
        "result": result,
    }
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client.connect(args.socket)
        client.sendall(json.dumps(message, ensure_ascii=False).encode("utf-8"))
        client.shutdown(socket.SHUT_WR)
        response = json.loads(client.recv(64 * 1024).decode("utf-8"))
    finally:
        client.close()
    print(json.dumps(response, ensure_ascii=False, sort_keys=True))
    return 0 if response.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
