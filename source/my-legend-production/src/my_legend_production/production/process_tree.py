"""Own a media process tree. The payload starts only after Windows Job containment."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from .repository import ProductionError


def media_environment():
    # Windows PowerShell must discover its own modules, even when the Worker was
    # started by PowerShell 7. Its Security module cannot load the newer runtime DLL.
    return {key: value for key, value in os.environ.items() if key.upper() != 'PSMODULEPATH' and not any(word in key.upper() for word in
        ("TOKEN", "PASSWORD", "SECRET", "API_KEY", "DSN", "DATABASE_URL", "VISUAL_MODEL_KEY", "AUDIO_MODEL_KEY"))}


class WindowsJob:
    def __init__(self):
        class Basic(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                ("flags", wintypes.DWORD), ("min_working", ctypes.c_size_t), ("max_working", ctypes.c_size_t),
                ("active_limit", wintypes.DWORD), ("affinity", ctypes.c_size_t),
                ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]
        class Io(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]
        class Extended(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", Io), ("process_memory", ctypes.c_size_t),
                ("job_memory", ctypes.c_size_t), ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        for name, args, result in (
            ("CreateJobObjectW", [ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            ("SetInformationJobObject", [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
            ("AssignProcessToJobObject", [wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            ("CloseHandle", [wintypes.HANDLE], wintypes.BOOL)):
            method = getattr(self.api, name)
            method.argtypes, method.restype = args, result
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ProductionError("PROCESS_CONTAINMENT_UNAVAILABLE")
        info = Extended()
        info.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE; no breakaway permitted.
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            self.close()
            raise ProductionError("PROCESS_CONTAINMENT_UNAVAILABLE")

    def assign(self, child):
        if not self.api.AssignProcessToJobObject(self.handle, wintypes.HANDLE(int(child._handle))):
            raise ProductionError("PROCESS_CONTAINMENT_UNAVAILABLE")

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def run_process_tree(command, *, timeout, log, env=None, cancel_file=None, check=None, input_text=None):
    """Synchronous exit code; cancel/deadline kills descendants, including FFmpeg.

    Payload arguments are data sent over stdin after containment, never shell text.
    Closing the non-inherited Job handle also cleans up children after Worker death.
    """
    if os.name != "nt":
        raise ProductionError("PROCESS_CONTAINMENT_PLATFORM_UNSUPPORTED")
    deadline = time.monotonic() + timeout
    next_check = 0.0
    def guard():
        nonlocal next_check
        if cancel_file is not None and Path(cancel_file).exists():
            raise ProductionError("CANCEL_REQUESTED")
        if time.monotonic() >= deadline:
            raise ProductionError("DEADLINE_EXCEEDED")
        if check and time.monotonic() >= next_check:
            check()
            next_check = time.monotonic() + 1
    guard()
    job, child = WindowsJob(), None
    try:
        child = subprocess.Popen([sys.executable, "-m", "my_legend_production.production.process_tree"],
            stdin=subprocess.PIPE, stdout=log, stderr=log, env=env,
            creationflags=subprocess.CREATE_NO_WINDOW, close_fds=True)
        job.assign(child)
        child.stdin.write((json.dumps({"command": list(map(str, command)), "input_text": input_text}) + "\n").encode("utf-8"))
        child.stdin.flush()
        while child.poll() is None:
            guard()
            time.sleep(.2)
        guard()
        return child.returncode
    finally:
        job.close()
        if child is not None:
            # If assignment failed the bootstrap has not received a payload and can be killed alone.
            if child.poll() is None:
                child.kill()
            child.wait(timeout=10)
            if child.stdin:
                child.stdin.close()


def main():
    message = sys.stdin.buffer.readline()
    if not message:
        return 1
    payload = json.loads(message)
    command = payload["command"]
    if not isinstance(command, list) or not command or any(not isinstance(item, str) for item in command):
        return 1
    if payload["input_text"] is not None:
        return subprocess.run(command, input=payload["input_text"], text=True, encoding="utf-8",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).returncode
    return subprocess.call(command, stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


if __name__ == "__main__":
    raise SystemExit(main())
