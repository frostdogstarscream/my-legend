"""Persistent sound-effect library: SQLite metadata and immutable WAV blobs.

Only exact request matches are eligible here. Similarity search, generation and
provider retries are deliberately owned by callers, not this storage component.
"""
from hashlib import sha256
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from .contracts import Contract


class SoundLibraryRequest(Contract):
    schema_version: Literal['sound_library_request/1.0'] = 'sound_library_request/1.0'
    scope: str = Field(min_length=1,max_length=128)
    provider_endpoint: str
    model: str = Field(min_length=1,max_length=200)
    prompt: str = Field(min_length=1,max_length=12000)
    seed: int
    sample_rate: int = Field(ge=8000,le=192000)
    channels: Literal[1,2]
    format: Literal['wav'] = 'wav'
    candidate_version: int = Field(default=1,ge=1)
    request_revision: str = Field(default='qianwen_sfx_http/1',min_length=1,max_length=128)

    @model_validator(mode='after')
    def safe_endpoint(self):
        url=urlsplit(self.provider_endpoint)
        if url.scheme!='https' or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('SOUND_LIBRARY_ENDPOINT_INVALID')
        return self

    def cache_key(self):
        return sha256(json.dumps(self.model_dump(),ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


class SoundLibrary:
    def __init__(self,root):
        self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True)
        (self.root/'blobs').mkdir(exist_ok=True)
        self.database=self.root/'index.sqlite'
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS assets (
                    sha256 TEXT PRIMARY KEY, size_bytes INTEGER NOT NULL, frames INTEGER NOT NULL,
                    sample_rate INTEGER NOT NULL, channels INTEGER NOT NULL, peak REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS entries (
                    cache_key TEXT NOT NULL, asset_sha256 TEXT NOT NULL REFERENCES assets(sha256),
                    request_json TEXT NOT NULL, review_state TEXT NOT NULL CHECK(review_state IN ('pending','approved','rejected')),
                    created_at REAL NOT NULL, PRIMARY KEY(cache_key,asset_sha256));
                CREATE TABLE IF NOT EXISTS origins (
                    cache_key TEXT NOT NULL, asset_sha256 TEXT NOT NULL, job_id TEXT NOT NULL,
                    operation_id TEXT NOT NULL, created_at REAL NOT NULL,
                    PRIMARY KEY(cache_key,asset_sha256,job_id,operation_id));
                CREATE TABLE IF NOT EXISTS review_events (
                    id INTEGER PRIMARY KEY, cache_key TEXT NOT NULL, asset_sha256 TEXT NOT NULL,
                    state TEXT NOT NULL, actor TEXT NOT NULL, note TEXT NOT NULL, created_at REAL NOT NULL);
            ''')

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.database,timeout=15)
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def publish(self,request:SoundLibraryRequest,path,*,job_id,operation_id):
        """Store a generated raw sound. Publishing never implies human approval."""
        import numpy as np
        import soundfile as sf
        if not job_id or not operation_id:
            raise ValueError('SOUND_LIBRARY_PROVENANCE_REQUIRED')
        path=Path(path)
        if not 0<path.stat().st_size<=256*1024*1024:
            raise ValueError('SOUND_LIBRARY_SIZE_INVALID')
        raw=path.read_bytes();digest=sha256(raw).hexdigest()
        # Decode the same bytes that are hashed/stored, avoiding a source-file race.
        from io import BytesIO
        with sf.SoundFile(BytesIO(raw)) as stream:
            if stream.format!='WAV' or stream.frames<=0 or (stream.samplerate,stream.channels)!=(request.sample_rate,request.channels):
                raise ValueError('SOUND_LIBRARY_AUDIO_INVALID')
            frames,rate,channels=stream.frames,stream.samplerate,stream.channels
            peak=0.0
            for block in stream.blocks(blocksize=65536,dtype='float64',always_2d=True):
                if not np.isfinite(block).all():
                    raise ValueError('SOUND_LIBRARY_PCM_INVALID')
                peak=max(peak,float(np.abs(block).max()))
        if peak==0:
            raise ValueError('SOUND_LIBRARY_SILENT_AUDIO')
        key=request.cache_key();destination=self.root/'blobs'/(digest+'.wav')
        # Serialize publication across writers; a crash before commit leaves an unindexed blob only.
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if destination.exists():
                if sha256(destination.read_bytes()).hexdigest()!=digest:
                    raise ValueError('SOUND_LIBRARY_BLOB_CORRUPT')
            else:
                with destination.open('xb') as output:output.write(raw)
            db.execute('INSERT OR IGNORE INTO assets VALUES(?,?,?,?,?,?)',(digest,len(raw),frames,rate,channels,peak))
            db.execute('INSERT OR IGNORE INTO entries VALUES(?,?,?,?,?)',
                (key,digest,json.dumps(request.model_dump(),ensure_ascii=False,sort_keys=True),'pending',time.time()))
            db.execute('INSERT OR IGNORE INTO origins VALUES(?,?,?,?,?)',(key,digest,job_id,operation_id,time.time()))
        return dict(cache_key=key,asset_sha256=digest,frames=frames,sample_rate=rate,channels=channels,
                    duration_seconds=frames/rate,review_state=self.entry(key,digest)['review_state'])

    def entry(self,key,digest):
        with self.connect() as db:
            row=db.execute('SELECT e.*,a.frames,a.sample_rate,a.channels,a.peak,a.size_bytes FROM entries e JOIN assets a ON a.sha256=e.asset_sha256 WHERE cache_key=? AND asset_sha256=?',(key,digest)).fetchone()
        if row is None:raise ValueError('SOUND_LIBRARY_ENTRY_NOT_FOUND')
        return dict(row)

    def lookup(self,request:SoundLibraryRequest,*,allow_unreviewed=False):
        with self.connect() as db:
            rows=db.execute("SELECT asset_sha256 FROM entries WHERE cache_key=? AND review_state IN (?,?) ORDER BY CASE review_state WHEN 'approved' THEN 0 ELSE 1 END,created_at,asset_sha256",
                (request.cache_key(),'approved','pending' if allow_unreviewed else 'approved')).fetchall()
        for row in rows:
            item=self.entry(request.cache_key(),row['asset_sha256'])
            path=self.root/'blobs'/(item['asset_sha256']+'.wav')
            if not path.is_file() or sha256(path.read_bytes()).hexdigest()!=item['asset_sha256']:
                continue # Never offer damaged assets; other historical candidates may remain valid.
            item.pop('request_json')
            return dict(item,path=str(path),match='exact_request',human_review=item['review_state'])
        return None

    def review(self,key,digest,*,state,actor,note):
        if state not in ('approved','rejected','pending') or not actor.strip() or not note.strip():
            raise ValueError('SOUND_LIBRARY_REVIEW_INVALID')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            changed=db.execute('UPDATE entries SET review_state=? WHERE cache_key=? AND asset_sha256=?',(state,key,digest)).rowcount
            if changed!=1:raise ValueError('SOUND_LIBRARY_ENTRY_NOT_FOUND')
            db.execute('INSERT INTO review_events(cache_key,asset_sha256,state,actor,note,created_at) VALUES(?,?,?,?,?,?)',
                       (key,digest,state,actor,note,time.time()))
