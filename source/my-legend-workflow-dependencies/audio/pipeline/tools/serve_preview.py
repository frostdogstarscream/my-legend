"""Loopback-only review server. No model calls and no external publication."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--revision", type=int, default=1)
    args = p.parse_args()
    root = args.root.resolve()
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(root)))
    info = {"pid": os.getpid(), "url": f"http://127.0.0.1:{server.server_port}/drafts/r{args.revision}/preview.html", "bind": "127.0.0.1", "revision": args.revision}
    (root / "server_info.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(info), flush=True)
    server.serve_forever()
