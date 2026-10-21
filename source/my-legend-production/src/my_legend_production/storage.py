from __future__ import annotations

import os
from pathlib import Path
import re
import sqlite3
import tempfile

from .core import AdapterError, json_bytes, parse_json, require, sha


class ArtifactStore:
    """One store per trusted caller; only registered content-addressed artifacts resolve."""
    def __init__(self, root, fixture_root=None):
        self.root = Path(root).resolve()
        self.objects = self.root / 'objects'
        self.objects.mkdir(parents=True, exist_ok=True)
        self.fixture_root = Path(fixture_root).resolve() if fixture_root else None

    def put(self, data: bytes):
        digest = sha(data)
        target = self.objects / digest
        if target.exists():
            require(sha(target.read_bytes()) == digest, 'ARTIFACT_CORRUPTED')
        else:
            fd, temp = tempfile.mkstemp(dir=self.objects, prefix='.write-')
            try:
                with os.fdopen(fd, 'wb') as out:
                    out.write(data); out.flush(); os.fsync(out.fileno())
                # Same content under the same digest is safe under concurrent registration.
                if target.exists():
                    require(sha(target.read_bytes()) == digest, 'ARTIFACT_CORRUPTED')
                else:
                    os.replace(temp, target)
            finally:
                Path(temp).unlink(missing_ok=True)
        return {'ref': 'artifact://' + digest, 'sha256': digest}

    def put_json(self, value):
        return self.put(json_bytes(value))

    def register(self, path):
        # Path is supplied by the trusted local operator, never from story text.
        return self.put(Path(path).read_bytes())

    def read(self, reference):
        uri, digest = reference['ref'], reference['sha256']
        require(bool(re.fullmatch('[0-9a-f]{64}', digest)), 'SOURCE_HASH_MISMATCH')
        if uri.startswith('fixture://'):
            require(self.fixture_root is not None, 'FIXTURE_REFERENCE_FORBIDDEN')
            suffix = uri[len('fixture://'):]
            require('\\' not in suffix and ':' not in suffix and '%' not in suffix, 'PATH_ESCAPE')
            path = (self.fixture_root / suffix).resolve()
            require(path.is_relative_to(self.fixture_root) and path.is_file(), 'PATH_ESCAPE')
        else:
            require(uri == 'artifact://' + digest, 'ARTIFACT_REFERENCE_INVALID')
            path = self.objects / digest
            require(path.is_file(), 'RESOURCE_TEMPORARILY_UNAVAILABLE', state='needs_context', retryable=True)
        data = path.read_bytes()
        require(sha(data) == digest, 'SOURCE_HASH_MISMATCH')
        return data

    def json(self, reference):
        return parse_json(self.read(reference))

    def verify_tree(self, value):
        if isinstance(value, dict):
            if set(value) == {'ref', 'sha256'}:
                self.read(value)
            else:
                for child in value.values(): self.verify_tree(child)
        elif isinstance(value, list):
            for child in value: self.verify_tree(child)

    def database(self):
        connection = sqlite3.connect(self.root / 'preparations.sqlite', timeout=30)
        connection.execute('CREATE TABLE IF NOT EXISTS prepared (caller TEXT, adapter TEXT, idem TEXT, fingerprint TEXT, result BLOB, request BLOB, PRIMARY KEY(caller,adapter,idem))')
        return connection

