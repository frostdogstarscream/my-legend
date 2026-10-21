"""Loopback-only hidden credential entry and one in-memory live production run."""
from __future__ import annotations

import argparse
import contextlib
import html
import io
import json
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from lingban_pipeline.cli import main as cli_main


def serve(args):
    root = Path(args.root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    token = args.token or secrets.token_urlsafe(24)
    csrf = secrets.token_urlsafe(32)
    guard = threading.Lock()
    state = {"status": "awaiting_credentials", "requests_started": False}
    page = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>灵伴 · 真实模型联调</title>
<style>[hidden]{display:none!important}body{font-family:system-ui,'Microsoft YaHei',sans-serif;background:#f3f1fa;color:#29223b;max-width:860px;margin:48px auto;padding:0 24px}main{background:white;border-radius:20px;padding:32px;box-shadow:0 8px 32px #31224d12}h1{margin-top:0}p{line-height:1.8}label{display:block;margin:20px 0 8px}input{width:100%;box-sizing:border-box;padding:14px;border:1px solid #ccc5dc;border-radius:9px;font-size:16px}button,a.button{background:#7152ad;color:white;border:0;border-radius:9px;padding:13px 22px;cursor:pointer;display:inline-block;text-decoration:none;margin-top:22px}pre{white-space:pre-wrap;background:#f5f3f9;padding:16px;border-radius:8px}small{color:#655d74}button:disabled{opacity:.5}</style>
<main><h1>灵伴 · 真实模型联调</h1><p>本次用现有样稿的三句对白、扳手碰桌和关门拟音，验证表演加工、声音生成、实测排轴与草稿混音。</p>
<p><small>Key 在本地运行进程中临时使用。输入内容不会写入项目文件、运行日志或制作清单。此页面仅在本机开放。</small></p>
<form method="post" action="TOKEN_PATH/start" autocomplete="off" id="form">
<input type="hidden" name="csrf" value="CSRF_VALUE">
<label for="ds">DeepSeek 官方平台 Key</label><input id="ds" type="password" name="deepseek" required autocomplete="off" spellcheck="false">
<label for="qw">千问 AI 平台 Key</label><input id="qw" type="password" name="qianwen" required autocomplete="off" spellcheck="false">
<button id="start">开始真实联调</button></form><pre id="status">等待填写凭据</pre><a class="button" id="preview" href="TOKEN_PATH/media/drafts/r1/preview.html" hidden>打开草稿试听</a>
</main><script>
const form=document.getElementById('form');
form.addEventListener('submit',async(e)=>{e.preventDefault();const btn=document.getElementById('start');btn.disabled=true;try{const r=await fetch(form.action,{method:'POST',body:new URLSearchParams(new FormData(form)),headers:{'Content-Type':'application/x-www-form-urlencoded'}});form.reset();const data=await r.json();if(!r.ok){btn.disabled=false;document.getElementById('status').textContent=data.message||'提交失败';}}catch(e){form.reset();btn.disabled=false;document.getElementById('status').textContent='本地服务未收到提交，请重试';}});
async function refresh(){try{const r=await fetch('TOKEN_PATH/status',{cache:'no-store'});const s=await r.json();const names={awaiting_credentials:'等待填写凭据',running:'正在进行真实模型联调',awaiting_review:'草稿已生成，等待人工试听',needs_input:'工作流需要处理接口或输入问题',stopped:'执行停止，待检查任务记录'};document.getElementById('status').textContent=(names[s.status]||s.status)+'\n'+(s.summary?JSON.stringify(s.summary,null,2):'');if(s.preview_available)document.getElementById('preview').hidden=false;}catch(e){document.getElementById('status').textContent='无法连接本地服务';}}
setInterval(refresh,3000);refresh();</script></html>""".replace("TOKEN_PATH", "/" + token).replace("CSRF_VALUE", csrf)

    def run(keys):
        import sys
        inserted = []
        original_argv = sys.argv
        output = io.StringIO()
        try:
            for name, value in keys.items():
                os.environ[name] = value
                inserted.append(name)
            keys.clear()
            sys.argv = ["lingban", "run", "--input", args.input, "--config", args.config, "--root", str(root), "--thread", args.thread]
            with contextlib.redirect_stdout(output):
                cli_main()
            # CLI only emits a compact, credential-free result, not SDK errors.
            result = json.loads(output.getvalue())
            with guard:
                state.update(status=result.get("status", "stopped"), summary=result)
        except BaseException as exc:
            with guard:
                state.update(status="stopped", summary={"error_type": type(exc).__name__, "message": "Inspect the workflow state and task ledger; no raw provider error is logged."})
        finally:
            sys.argv = original_argv
            for name in inserted:
                os.environ.pop(name, None)
            output.close()
            with guard:
                state["credentials_cleared"] = True

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *unused):
            pass

        def send(self, status, content, kind="application/json; charset=utf-8"):
            body = content.encode("utf-8") if isinstance(content, str) else content
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def valid_host(self):
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

        def do_GET(self):
            path = unquote(urlsplit(self.path).path)
            if not self.valid_host() or not path.startswith("/" + token + "/"):
                return self.send(404, "{}")
            if path == "/" + token + "/":
                with guard:
                    public = dict(state)
                rendered = page
                if public["status"] != "awaiting_credentials":
                    summary = html.escape(json.dumps(public, ensure_ascii=False, indent=2))
                    rendered = rendered.replace('id="form"', 'id="form" hidden')
                    rendered = rendered.replace('等待填写凭据</pre>', summary + '</pre>')
                    rendered = rendered.replace('<meta charset="utf-8">', '<meta charset="utf-8"><meta http-equiv="refresh" content="5">')
                if (root / "drafts/r1/preview.html").exists():
                    rendered = rendered.replace('id="preview" href=', 'id="preview" href=').replace('preview.html" hidden', 'preview.html"')
                return self.send(200, rendered, "text/html; charset=utf-8")
            if path == "/" + token + "/status":
                with guard:
                    public = dict(state)
                public["preview_available"] = (root / "drafts/r1/preview.html").exists()
                return self.send(200, json.dumps(public, ensure_ascii=False))
            prefix = "/" + token + "/media/"
            if path.startswith(prefix):
                target = (root / path[len(prefix):]).resolve()
                if not target.is_relative_to(root) or not target.is_file() or target.suffix.lower() not in {".wav", ".mp3", ".html", ".json", ".csv", ".vtt", ".md"}:
                    return self.send(404, "{}")
                import mimetypes
                return self.send(200, target.read_bytes(), mimetypes.guess_type(str(target))[0] or "application/octet-stream")
            self.send(404, "{}")

        def do_POST(self):
            expected_origin = f"http://127.0.0.1:{self.server.server_port}"
            # Embedded browsers may omit Origin or use an opaque origin. The
            # independently generated hidden CSRF nonce is required in all cases.
            if not self.valid_host() or self.headers.get("Origin") not in {None, "null", expected_origin} or self.path != "/" + token + "/start":
                return self.send(403, '{"message":"Local interactive entry required"}')
            try:
                size = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return self.send(400, "{}")
            if not 0 < size <= 8192 or not self.headers.get("Content-Type", "").startswith("application/x-www-form-urlencoded"):
                return self.send(400, "{}")
            try:
                fields = parse_qs(self.rfile.read(size).decode("utf-8"), max_num_fields=3)
            except (ValueError, UnicodeDecodeError):
                return self.send(400, "{}")
            if not secrets.compare_digest(fields.get("csrf", [""])[0], csrf):
                return self.send(403, '{"message":"Refresh the local entry page and submit its form"}')
            ds, qw = fields.get("deepseek", [""])[0].strip(), fields.get("qianwen", [""])[0].strip()
            if not ds or not qw:
                return self.send(400, '{"message":"Both credentials are required"}')
            with guard:
                if state["status"] != "awaiting_credentials":
                    return self.send(409, '{"message":"This run has already started"}')
                state.update(status="running", requests_started=True)
            threading.Thread(target=run, args=({"DEEPSEEK_API_KEY": ds, "DASHSCOPE_API_KEY": qw},), daemon=True).start()
            if self.headers.get("Sec-Fetch-Mode") == "navigate" or "text/html" in self.headers.get("Accept", ""):
                self.send_response(303)
                self.send_header("Location", "/" + token + "/")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                self.send(202, '{"status":"running"}')

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{server.server_port}/{token}/"
    Path(args.ready).write_text(json.dumps({"url": url, "pid": os.getpid(), "root": str(root), "thread": args.thread, "credentials_persisted": False}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"url": url, "credentials_persisted": False}), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("input", "config", "root", "thread", "ready"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--token")
    serve(parser.parse_args())
