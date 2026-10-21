"""Content-addressed immutable artifacts; paths never enter the public protocol."""
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile

from .contracts import ArtifactRef
from .repository import ProductionError


class ArtifactStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, kind, version, data: bytes, media_type="application/json"):
        digest = sha256(data).hexdigest()
        target = self.root / digest
        if not target.exists():
            fd, tmp = tempfile.mkstemp(dir=self.root, prefix=".incoming-")
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
                # Concurrent producers have identical bytes under the same digest.
                os.replace(tmp, target)
            finally:
                Path(tmp).unlink(missing_ok=True)
        ref = ArtifactRef(kind=kind, sha256=digest, source_version_id=version, media_type=media_type, size_bytes=len(data))
        self.verify(ref)
        return ref

    def put_json(self, kind, version, value):
        return self.put(kind, version, json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode())

    def verify(self, ref: ArtifactRef):
        path = self.root / ref.sha256
        if not path.is_file() or path.stat().st_size != ref.size_bytes:
            raise ProductionError("ARTIFACT_MISSING")
        digest = sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != ref.sha256:
            raise ProductionError("ARTIFACT_HASH_MISMATCH")
        return path

    def json(self, ref):
        return json.loads(self.verify(ref).read_text(encoding="utf-8"))
