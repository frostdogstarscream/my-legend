"""Small explicit HTTP client. No automatic approvals or billable retries.

Run from the repository root. Credentials are read only from the environment.
"""
import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path


def send(method, path, body=None):
    base = os.environ.get("STORY_API_URL", "http://127.0.0.1:8000").rstrip("/")
    key = os.environ.get("STORY_API_KEY", "")
    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(base + path, method=method, headers=headers,
        data=None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8"))
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if "application/json" in response.headers.get("Content-Type", "") else raw
    except urllib.error.HTTPError as exc:
        # Server errors are already redacted. Never log request headers.
        raise RuntimeError(f"HTTP {exc.code}: {exc.read().decode('utf-8')}") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    call = sub.add_parser("send")
    call.add_argument("method", choices=["GET", "POST", "PATCH"])
    call.add_argument("path")
    call.add_argument("--body", type=Path)
    call.add_argument("--output", type=Path)
    poll = sub.add_parser("poll")
    poll.add_argument("kind", choices=["tasks", "jobs"])
    poll.add_argument("id")
    poll.add_argument("--seconds", type=int, default=1800)
    poll.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command == "send":
        result = send(args.method, args.path,
                      json.loads(args.body.read_text(encoding="utf-8-sig")) if args.body else None)
    else:
        deadline = time.monotonic() + args.seconds
        while True:
            result = send("GET", f"/v1/{args.kind}/{args.id}")
            print(f"{result['status']} {result['progress_pct']}% {result['stage']}")
            if result["status"] in ("SUCCEEDED", "FAILED", "CANCELLED"):
                break
            if time.monotonic() >= deadline:
                raise SystemExit("Polling stopped; task still exists. Resume polling with the same ID.")
            time.sleep(3)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) if not isinstance(result, str) else result
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered)
    if isinstance(result, dict) and result.get("status") in ("FAILED", "CANCELLED"):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
