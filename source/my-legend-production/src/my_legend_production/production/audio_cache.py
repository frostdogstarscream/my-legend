"""Immutable raw-result transfer for audio revisions; never transfers calls/checkpoints."""
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from zipfile import ZipFile, ZIP_STORED

from ..core import require
from ..worker import save


def event_key(event):
    return json.dumps([event[k] for k in ("episode_id", "scene_id", "line_id")], ensure_ascii=False, separators=(",", ":"))


def export_cache(root, batches, binding, candidates):
    entries, blobs = {}, {}
    for batch in batches:
        if 'audio_script' in batch and not any(e['kind'] in ('speech','sfx','ambience','music') for e in batch['audio_script']['events']):
            continue
        directory = Path(batch["path"]) / "audio-workflow"
        with sqlite3.connect((directory / "tasks.sqlite").resolve().as_uri() + "?mode=ro", uri=True) as db:
            rows = db.execute("SELECT id,result FROM tasks WHERE status='success' AND id LIKE 'raw_%'").fetchall()
        for task_id, encoded in rows:
            result = json.loads(encoded)
            if result.get("origin") not in ("generated","sound_library"):
                continue
            require(bool(re.fullmatch(r"raw_[a-f0-9]{64}", task_id)), "AUDIO_CACHE_TASK_INVALID")
            path = (directory / result["file"]).resolve()
            require(path.is_relative_to(directory.resolve()), "AUDIO_CACHE_PATH_INVALID")
            raw = path.read_bytes()
            digest = sha256(raw).hexdigest()
            require(digest == result["sha256"], "AUDIO_CACHE_HASH_MISMATCH")
            # Only processing metadata needed by the native reader is transferred.
            public = {key: result[key] for key in ("origin", "keypoints", "model", "fixture_only", "library_reference") if key in result}
            public.update(file=f"assets/raw/{digest}.wav", sha256=digest)
            if task_id in entries:
                require(entries[task_id] == public, "AUDIO_CACHE_CONFLICT")
            entries[task_id], blobs[digest] = public, raw
    compound=any('operation_map' in batch for batch in batches)
    document = dict(schema_version="audio_raw_cache/2.0" if compound else "audio_raw_cache/1.0", binding=binding, candidates=candidates, entries=entries)
    path = root / "output/raw-cache.zip"
    # ZIP_STORED and fixed timestamps give deterministic bytes for replay.
    from zipfile import ZipInfo
    with ZipFile(path, "w", compression=ZIP_STORED) as archive:
        archive.writestr(ZipInfo("manifest.json"), json.dumps(document, ensure_ascii=False, sort_keys=True).encode())
        for digest, raw in sorted(blobs.items()):
            archive.writestr(ZipInfo(f"blobs/{digest}.wav"), raw)
    return {"sha256": sha256(path.read_bytes()).hexdigest(), "entries": len(entries)}


def load_cache(path, binding):
    with ZipFile(path) as archive:
        infos = archive.infolist()
        require(len(infos) <= 5001 and sum(i.file_size for i in infos) <= 2_000_000_000, "AUDIO_CACHE_LIMIT_EXCEEDED")
        require(len({i.filename for i in infos}) == len(infos), "AUDIO_CACHE_DUPLICATE_MEMBER")
        document = json.loads(archive.read("manifest.json"))
        require(document.get("schema_version") in ("audio_raw_cache/1.0","audio_raw_cache/2.0"), "AUDIO_CACHE_SCHEMA_INVALID")
        if document['schema_version']=='audio_raw_cache/2.0':
            require(bool(binding.get('arrangement_sha256')) and document['binding'].get('arrangement_sha256')==binding['arrangement_sha256'],
                    'AUDIO_CACHE_ARRANGEMENT_MISMATCH')
        for key in ("source_version_id", "delivery_sha256", "manifest_sha256"):
            require(document["binding"][key] == binding[key], "AUDIO_CACHE_SOURCE_MISMATCH")
        blobs = {}
        for task_id, result in document["entries"].items():
            require(bool(re.fullmatch(r"raw_[a-f0-9]{64}", task_id)), "AUDIO_CACHE_TASK_INVALID")
            digest = result["sha256"]
            require(bool(re.fullmatch(r"[a-f0-9]{64}", digest)) and result["file"] == f"assets/raw/{digest}.wav"
                    and result["origin"] in ("generated","sound_library"), "AUDIO_CACHE_PATH_INVALID")
            if result['origin']=='sound_library':
                reference=result.get('library_reference',{})
                require(set(reference)=={'cache_key','asset_sha256','review_state'} and
                    all(isinstance(reference[k],str) and re.fullmatch(r'[a-f0-9]{64}',reference[k]) for k in ('cache_key','asset_sha256')) and
                    reference['asset_sha256']==digest and reference['review_state'] in ('pending','approved'),'AUDIO_CACHE_LIBRARY_REFERENCE_INVALID')
            raw = archive.read(f"blobs/{digest}.wav")
            require(sha256(raw).hexdigest() == digest, "AUDIO_CACHE_HASH_MISMATCH")
            blobs[digest] = raw
        return document, blobs


def seed_cache(directory, document, blobs):
    from lingban_pipeline.registry import Registry
    registry = Registry(directory)
    imported = 0
    for task_id, result in document["entries"].items():
        current = registry.get(task_id)
        if current:
            # Never overwrite live, ambiguous, or failed submissions on a resume.
            continue
        path = directory / result["file"]
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            require(sha256(path.read_bytes()).hexdigest() == result["sha256"], "AUDIO_CACHE_HASH_MISMATCH")
        else:
            path.write_bytes(blobs[result["sha256"]])
        registry.set(task_id, "success", 0, result)
        imported += 1
    return imported


def apply_candidates(batches, delivery, previous, regenerate, nonce):
    if any('operation_map' in batch for batch in batches):
        return apply_operation_candidates(batches,delivery,previous,regenerate,nonce)
    events = {event_key({"episode_id": ep["episode_id"], "scene_id": sc["scene_id"], "line_id": u["line_id"]}): u
              for ep in delivery["episodes"] for sc in ep["scenes"] for u in sc["utterances"]}
    require(set(previous) <= set(events), "AUDIO_CACHE_SOURCE_MISMATCH")
    selected = {event_key(e) for e in regenerate}
    require(selected <= set(events), "AUDIO_REMAKE_EVENT_NOT_FOUND")
    require(all(events[key]["kind"] in ("dialogue", "narration", "sfx") for key in selected), "AUDIO_REMAKE_EVENT_UNSUPPORTED")
    candidates = dict(previous)
    for key in selected:
        # Stable within one idempotent job, distinct between intentional remakes.
        candidates[key] = 2 + int(sha256((nonce + key).encode()).hexdigest()[:12], 16)
    for batch in batches:
        script = batch["audio_script"]
        by_id = {e["block_id"]: e for e in script["events"]}
        for coverage in batch["audio_map"]["coverage"]:
            candidate = candidates.get(event_key(coverage["source"]))
            if candidate is not None:
                require(isinstance(candidate, int) and candidate >= 1, "AUDIO_CACHE_CANDIDATE_INVALID")
                by_id[coverage["target_block_ids"][0]]["media"]["candidate_version"] = candidate
        save(Path(batch["path"]) / "script.json", script)
    return candidates


def apply_operation_candidates(batches,delivery,previous,regenerate,nonce):
    from .compound_audio import AUDIBLE_MODES
    source_keys={event_key(dict(episode_id=ep['episode_id'],scene_id=sc['scene_id'],line_id=u['line_id']))
                 for ep in delivery['episodes'] for sc in ep['scenes'] for u in sc['utterances']}
    operation_rows=[op for batch in batches for op in batch['operation_map']]
    require({event_key(op['source']) for op in operation_rows}==source_keys,'AUDIO_CACHE_SOURCE_MISMATCH')
    def operation_key(op):
        return json.dumps([op['source'][k] for k in ('episode_id','scene_id','line_id')]+[op['operation_id']],
                          ensure_ascii=False,separators=(',',':'))
    audible={operation_key(op):op for op in operation_rows if op['mode'] in AUDIBLE_MODES}
    require(set(previous)<=set(audible),'AUDIO_CACHE_OPERATION_MISMATCH')
    selected=set()
    for selection in regenerate:
        source=event_key(selection)
        require(source in source_keys,'AUDIO_REMAKE_EVENT_NOT_FOUND')
        matches={k for k,op in audible.items() if event_key(op['source'])==source and
                 (not selection.get('operation_id') or op['operation_id']==selection['operation_id'])}
        require(bool(matches),'AUDIO_REMAKE_OPERATION_UNSUPPORTED')
        require(not selected.intersection(matches),'AUDIO_REMAKE_SELECTION_OVERLAP')
        selected.update(matches)
    candidates=dict(previous)
    for key in selected:
        candidates[key]=2+int(sha256((nonce+key).encode()).hexdigest()[:12],16)
    for batch in batches:
        by_block={e['block_id']:e for e in batch['audio_script']['events']}
        for op in batch['operation_map']:
            candidate=candidates.get(operation_key(op))
            if candidate is not None:
                require(type(candidate) is int and candidate>=1,'AUDIO_CACHE_CANDIDATE_INVALID')
                by_block[op['block_id']]['media']['candidate_version']=candidate
        save(Path(batch['path'])/'script.json',batch['audio_script'])
    return candidates
