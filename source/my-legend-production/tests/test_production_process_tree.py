"""Actual Windows processes, no network, providers or production task mutation."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from my_legend_production.production.process_tree import run_process_tree, WindowsJob, media_environment
from my_legend_production.production.repository import ProductionError

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows Job Object acceptance")


def test_media_bootstrap_does_not_inherit_model_or_database_credentials(monkeypatch):
    for name in ("DRAMA_LLM_API_KEY", "DASHSCOPE_API_KEY", "MY_LEGEND_VISUAL_MODEL_KEY", "MY_LEGEND_AUDIO_MODEL_KEY", "PGPASSWORD", "MY_LEGEND_PRODUCTION_DSN"):
        monkeypatch.setenv(name, "fixture-only-secret")
    monkeypatch.setenv("MY_LEGEND_WORKFLOW_PATHS", "fixture-path")
    environment = media_environment()
    assert not any(value == "fixture-only-secret" for value in environment.values())
    assert environment["MY_LEGEND_WORKFLOW_PATHS"] == "fixture-path"


def test_windows_powershell_security_module_uses_its_own_runtime(monkeypatch, tmp_path):
    monkeypatch.setenv('PSModulePath', str(tmp_path/'incompatible-modules'))
    with (tmp_path/'security.log').open('w+b') as log:
        assert run_process_tree(['powershell.exe','-NoProfile','-Command',
            "$ErrorActionPreference='Stop'; Import-Module Microsoft.PowerShell.Security; "
            "$value=ConvertTo-SecureString 'fixture-only' -AsPlainText -Force; $value.Dispose(); Write-Output 'ok'"],
            timeout=15,log=log,env=media_environment()) == 0
        log.seek(0)
        assert log.read().strip()==b'ok'

PAYLOAD = '''import pathlib, subprocess, sys, time
root = pathlib.Path(sys.argv[1])
leaf = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
(root / 'leaf.pid').write_text(str(leaf.pid))
while not (root / 'release').exists(): time.sleep(.05)
'''


class ProcessHandle:
    def __init__(self, pid):
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.api.OpenProcess.restype = wintypes.HANDLE
        self.api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.api.WaitForSingleObject.restype = wintypes.DWORD
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.api.OpenProcess(0x100000, False, pid)
        assert self.handle

    def stopped(self):
        try:
            assert self.api.WaitForSingleObject(self.handle, 5000) == 0, "descendant is still running"
        finally:
            self.api.CloseHandle(self.handle)


@pytest.mark.parametrize("reason", ["cancel", "deadline", "normal", "lease"])
def test_cancel_deadline_and_normal_exit_clean_up_descendants(tmp_path, reason):
    script = tmp_path / "payload.py"
    script.write_text(PAYLOAD, encoding="utf-8")
    handles = []
    def check():
        pid = tmp_path / "leaf.pid"
        if pid.exists() and pid.read_text() and not handles:
            handles.append(ProcessHandle(int(pid.read_text())))
            if reason == "cancel":
                (tmp_path / "cancel").touch()
            elif reason == "normal":
                (tmp_path / "release").touch()
            elif reason == "lease":
                raise ProductionError("LEASE_LOST")
    with (tmp_path / "output.log").open("wb") as log:
        def run():
            return run_process_tree([sys.executable, str(script), str(tmp_path)], timeout=3,
                log=log, cancel_file=tmp_path / "cancel", check=check)
        if reason == "normal":
            assert run() == 0
        else:
            with pytest.raises(ProductionError, match={"cancel": "CANCEL_REQUESTED", "deadline": "DEADLINE_EXCEEDED", "lease": "LEASE_LOST"}[reason]):
                run()
    assert len(handles) == 1
    handles[0].stopped()


def test_unicode_input_is_forwarded_without_shell_interpretation(tmp_path):
    text = '剧情数据：$() ` ; "quoted"\n第二行'
    with (tmp_path / "output.log").open("w+b") as log:
        assert run_process_tree([sys.executable, "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"],
            input_text=text, timeout=5, log=log) == 0
        log.seek(0)
        assert log.read().decode("utf-8").replace("\r\n", "\n") == text


def test_payload_does_not_start_if_containment_fails(tmp_path, monkeypatch):
    def reject(self, child):
        raise ProductionError("PROCESS_CONTAINMENT_UNAVAILABLE")
    monkeypatch.setattr(WindowsJob, "assign", reject)
    marker = tmp_path / "must-not-exist"
    with (tmp_path / "output.log").open("wb") as log:
        with pytest.raises(ProductionError, match="PROCESS_CONTAINMENT_UNAVAILABLE"):
            run_process_tree([sys.executable, "-c", "import pathlib,sys; pathlib.Path(sys.argv[1]).touch()", str(marker)], timeout=3, log=log)
    assert not marker.exists()


def test_worker_process_death_closes_job_and_terminates_descendants(tmp_path):
    payload = tmp_path / "payload.py"
    payload.write_text(PAYLOAD, encoding="utf-8")
    controller = tmp_path / "controller.py"
    controller.write_text('''import pathlib, sys
from my_legend_production.production.process_tree import run_process_tree
root=pathlib.Path(sys.argv[1])
with (root/'child.log').open('wb') as log:
    run_process_tree([sys.executable,str(root/'payload.py'),str(root)],timeout=30,log=log)
''', encoding="utf-8")
    with (tmp_path / "controller.log").open("wb") as log:
        worker = subprocess.Popen([sys.executable, str(controller), str(tmp_path)], stdout=log, stderr=log,
                                  creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            deadline = time.monotonic()+10
            pid = tmp_path / "leaf.pid"
            while time.monotonic() < deadline and (not pid.exists() or not pid.read_text()):
                assert worker.poll() is None, "controller failed before starting contained payload"
                time.sleep(.05)
            handle = ProcessHandle(int(pid.read_text()))
            worker.kill()
            worker.wait(timeout=5)
            handle.stopped()
        finally:
            if worker.poll() is None:
                worker.kill()
                worker.wait(timeout=5)
