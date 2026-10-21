from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from urllib.parse import parse_qsl, urlparse
from pathlib import Path
from typing import Any


def digest(value: Any) -> str:
    if isinstance(value, bytes):
        data = value
    else:
        data = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(data).hexdigest()


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(native(path), "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def native(path: Path | str) -> str:
    """Keep manifest paths portable; use extended Windows paths only at IO boundaries."""
    # Extended Windows paths disable normal Win32 dot-segment resolution.
    # Collapse '.' and '..' before adding the prefix so CLI-relative inputs work.
    value = os.path.normpath(str(Path(path).absolute()))
    if os.name == "nt" and not value.startswith("\\\\?\\"):
        return "\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value
    return value


def load(path: Path):
    with open(native(path), encoding="utf-8-sig") as stream:
        return json.load(stream)


def save(path: Path, value: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    fd, temp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(data)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def safe_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", value) or ".." in value:
        raise ValueError("Invalid identifier")
    return value


def sensitive_text(value: str, allow_test_urls=False):
    if re.search(r"\bsk-[A-Za-z0-9_.-]{12,}", value) or "Bearer " in value:
        return True
    for url in re.findall(r"https?://[^\s\"'<>]+", value):
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if allow_test_urls and (host in {"example.org", "example.com", "example.net"} or host.endswith(".invalid")):
            continue
        query = {key.lower() for key, _ in parse_qsl(parsed.query)}
        if query & {"signature", "x-amz-signature", "x-oss-signature", "ossaccesskeyid", "awsaccesskeyid"}:
            return True
    return False


def sensitive_artifact_text(value: str):
    """Scan source packages while permitting code that builds an auth header.

    Runtime values still use the stricter sensitive_text check. Only reserved
    example domains are exempted from signed URL detection in test source.
    """
    if re.search(r"\bBearer\s+[A-Za-z0-9_.~+/=-]{12,}", value):
        return True
    return sensitive_text(value.replace("Bearer ", ""), allow_test_urls=True)


def safe_config(value: Any):
    """Reject credentials before they can enter checkpoints or export artifacts."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in {"api_key", "token", "authorization", "password", "secret", "access_token"}:
                raise ValueError("Credentials must come from environment variables")
            safe_config(item)
    elif isinstance(value, list):
        for item in value:
            safe_config(item)
    elif isinstance(value, str) and sensitive_text(value):
        raise ValueError("Credential or signed URL cannot enter state")


def under(root: Path, relative: str) -> Path:
    p = (root / relative).resolve()
    if not p.is_relative_to(root.resolve()):
        raise ValueError("Media reference escapes production root")
    return p
