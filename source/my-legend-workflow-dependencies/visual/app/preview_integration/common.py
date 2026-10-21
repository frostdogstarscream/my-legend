"""整合层共用的文件、哈希与受控子进程工具。"""
import hashlib
import json
import os
import subprocess
from pathlib import Path

class IntegrationError(ValueError):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)

def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

def child_env(extra=None):
    # 不把宿主模型凭据传给结构检查和本地联调进程；不会读取任何会话中的 Key。
    env = {k: os.environ[k] for k in ('PATH','LANG','LC_ALL','TMPDIR','SYSTEMROOT') if k in os.environ}
    return env | {'PYTHONDONTWRITEBYTECODE':'1','LANGSMITH_TRACING':'false'} | (extra or {})

def command(args, *, cwd, log, env=None, timeout=180):
    try:
        result = subprocess.run([str(a) for a in args], cwd=cwd, env=child_env(env),
                                capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise IntegrationError('PROCESS_TIMEOUT','阶段运行超时，输出与检查点保留；请显式恢复') from exc
    Path(log).write_text(result.stdout + '\n' + result.stderr, encoding='utf-8')
    if result.returncode:
        raise IntegrationError('PROCESS_FAILED',f'子进程失败，详情见 {Path(log).name}')
    return result.stdout

def within(root, name):
    root = Path(root).resolve()
    path = (root / name).resolve()
    if Path(name).is_absolute() or not path.is_relative_to(root):
        raise IntegrationError('PATH_ESCAPE','文件引用越出指定目录')
    return path
