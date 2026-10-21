from hashlib import sha256
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from my_legend_production.core import AdapterError
from my_legend_production.production.audio_cache import export_cache, load_cache, seed_cache, apply_candidates, event_key


@pytest.fixture
def registry(monkeypatch):
    root = Path(__file__).resolve().parents[3] / "runtime/my-legend-production/dependencies/audio/pipeline/src"
    monkeypatch.syspath_prepend(str(root))
    from lingban_pipeline.registry import Registry
    return Registry


@pytest.fixture
def cache(tmp_path, registry):
    root = tmp_path / "original"
    directory = root / "scenes/000/audio-workflow"
    directory.mkdir(parents=True)
    (root / "output").mkdir()
    raw = b"fixture raw bytes"
    digest = sha256(raw).hexdigest()
    path = directory / "assets/raw/one.wav"
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    result = dict(file="assets/raw/one.wav", sha256=digest, origin="generated", request_id="not-exported",
                  retrieval={"private_signed_url": "never-export"})
    ledger = registry(directory)
    ledger.set("raw_" + "a"*64, "success", 1, result)
    ledger.set("raw_" + "b"*64, "unknown", 1)
    binding = dict(source_version_id="v", delivery_sha256="d"*64, manifest_sha256="e"*64, voices={"one": "voice"})
    batches = [dict(path=str(directory.parent))]
    report = export_cache(root, batches, binding, {})
    return root, binding, batches, report


def test_transfer_only_verified_successful_raw_results_not_credentials_or_calls(cache, tmp_path, registry):
    root, binding, batches, report = cache
    raw = (root / "output/raw-cache.zip").read_bytes()
    assert b"never-export" not in raw and b"not-exported" not in raw
    document, blobs = load_cache(root / "output/raw-cache.zip", binding)
    assert len(document["entries"]) == 1
    destination = tmp_path / "new-job"
    assert seed_cache(destination, document, blobs) == 1
    assert registry(destination).get("raw_" + "a"*64)["attempts"] == 0
    assert registry(destination).get("raw_" + "b"*64) is None
    assert not (destination / "calls.sqlite").exists()
    assert not (destination / "checkpoints.sqlite").exists()
    assert export_cache(root, batches, binding, {}) == report


@pytest.mark.parametrize("state", ["running", "unknown", "failed"])
def test_cache_seed_does_not_overwrite_live_or_ambiguous_task(cache, tmp_path, registry, state):
    root, binding, _, _ = cache
    document, blobs = load_cache(root / "output/raw-cache.zip", binding)
    destination = tmp_path / "resumed"
    ledger = registry(destination)
    ledger.set("raw_" + "a"*64, state, 2)
    assert seed_cache(destination, document, blobs) == 0
    assert ledger.get("raw_" + "a"*64)["status"] == state


def test_cache_rejects_wrong_source_and_changed_raw_content(cache):
    root, binding, _, _ = cache
    with pytest.raises(AdapterError, match="AUDIO_CACHE_SOURCE_MISMATCH"):
        load_cache(root / "output/raw-cache.zip", binding | {"delivery_sha256": "f"*64})
    document, _ = load_cache(root / "output/raw-cache.zip", binding)
    result = next(iter(document["entries"].values()))
    bad = root / "bad.zip"
    with ZipFile(bad, "w") as z:
        z.writestr("manifest.json", json.dumps(document))
        z.writestr(f"blobs/{result['sha256']}.wav", b"changed")
    with pytest.raises(AdapterError, match="AUDIO_CACHE_HASH_MISMATCH"):
        load_cache(bad, binding)


def test_candidates_change_only_selected_events_and_keep_previous_revisions(tmp_path):
    lines = [dict(line_id=str(i), kind="dialogue") for i in range(3)]
    delivery = dict(episodes=[dict(episode_id="ep", scenes=[dict(scene_id="sc", utterances=lines)])])
    sources = [dict(episode_id="ep", scene_id="sc", line_id=str(i)) for i in range(3)]
    def batches():
        return [dict(path=str(tmp_path), audio_script=dict(events=[dict(block_id=str(i), media={}) for i in range(3)]),
            audio_map=dict(coverage=[dict(source=s, target_block_ids=[s["line_id"]]) for s in sources]))]
    previous = {event_key(sources[0]): 1234}
    current = batches()
    candidates = apply_candidates(current, delivery, previous, [sources[1]], "job-a")
    assert candidates[event_key(sources[0])] == 1234
    assert event_key(sources[2]) not in candidates
    assert current[0]["audio_script"]["events"][2]["media"] == {}
    assert apply_candidates(batches(), delivery, previous, [sources[1]], "job-a") == candidates
    assert apply_candidates(batches(), delivery, previous, [sources[1]], "job-b") != candidates
    bad = dict(sources[0], line_id="absent")
    with pytest.raises(AdapterError, match="AUDIO_REMAKE_EVENT_NOT_FOUND"):
        apply_candidates(batches(), delivery, {}, [bad], "job")
