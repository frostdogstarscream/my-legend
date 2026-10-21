"""Credential-entry boundary tests: no provider request is started."""
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def test_loopback_nonce_native_form_and_no_secret_persistence(tmp_path):
    pipeline = Path(__file__).resolve().parents[1]
    ready = tmp_path / "ready.json"
    root = tmp_path / "production"
    proc = subprocess.Popen(
        [sys.executable, str(pipeline / "tools/serve_live_session.py"),
         "--input", str(tmp_path / "unused-script.json"),
         "--config", str(tmp_path / "unused-config.json"),
         "--root", str(root), "--thread", "test-entry", "--ready", str(ready)],
        cwd=pipeline, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            assert proc.poll() is None
            time.sleep(.05)
        assert ready.exists()
        metadata = json.loads(ready.read_text(encoding="utf-8"))
        assert metadata["credentials_persisted"] is False
        url = metadata["url"]
        with urlopen(url, timeout=3) as response:
            page = response.read().decode("utf-8")
            assert response.headers["Cache-Control"] == "no-store"
        assert page.count('type="password"') == 2
        nonce = re.search(r'name="csrf" value="([^"]+)"', page).group(1)

        def post(fields, origin=None):
            headers = {"Content-Type": "application/x-www-form-urlencoded"}
            if origin is not None:
                headers["Origin"] = origin
            try:
                with urlopen(Request(url + "start", data=urlencode(fields).encode(), headers=headers), timeout=3) as response:
                    return response.status
            except HTTPError as exc:
                return exc.code

        assert post({"deepseek": "", "qianwen": ""}) == 403
        # Origin-less embedded/native submission is allowed only with the nonce;
        # empty credentials then fail validation before any task starts.
        assert post({"deepseek": "", "qianwen": "", "csrf": nonce}) == 400
        assert post({"deepseek": "", "qianwen": "", "csrf": nonce}, "null") == 400
        assert post({"deepseek": "", "qianwen": "", "csrf": nonce}, "https://foreign.example") == 403
        assert post({"deepseek": "", "qianwen": "", "csrf": "wrong"}) == 403
        assert post({"deepseek": "", "qianwen": "", "csrf": nonce, "extra": "x"}) == 400
        with urlopen(url + "status", timeout=3) as response:
            status = json.load(response)
        assert status["status"] == "awaiting_credentials"
        assert status["requests_started"] is False
        assert not (root / "tasks.sqlite").exists()
    finally:
        proc.terminate()
        proc.wait(timeout=10)
