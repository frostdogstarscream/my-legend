"""Offline regression using immutable historical audio; never a new-model acceptance run."""
from hashlib import sha256
import json
from pathlib import Path
import socket
import tempfile

from my_legend_production.performance_production import prepare_audio, read
from my_legend_production.production.audio_job import combine, usage_snapshot
from my_legend_production.worker import save


def main():
    workspace = Path(__file__).resolve().parents[2]
    runtime = workspace / "runtime/my-legend-production"
    source = runtime / "runs/story-20261019-001"
    destination = Path(tempfile.mkdtemp(prefix="verify-audio-cache-", dir=runtime))
    # This verifier may only consume local cached files.
    def offline(*args, **kwargs):
        raise RuntimeError("NETWORK_FORBIDDEN_IN_CACHE_TEST")
    socket.socket.connect = offline
    plan = read(source / "input/production-plan.json")
    def original(ref):
        raw = (source / "store/objects" / ref["sha256"]).read_bytes()
        assert sha256(raw).hexdigest() == ref["sha256"]
        return json.loads(raw)
    delivery = original(plan["source"]["delivery"])
    manifest = original(plan["source"]["render_manifest"])
    _, _, batches = prepare_audio(destination, delivery, manifest, plan["voices"], read(runtime / "workflows.json"))
    for batch in batches:
        batch["path"] = str(source / "scenes" / f'{batch["index"]:03d}')
    result = combine(destination, batches, read(source / "audio-results.json"), delivery)
    result.update(verification="historical_audio_cache_regression", new_model_calls=0,
                  independent_acceptance=False, historical_usage=usage_snapshot(source))
    save(destination / "verification.json", result)
    print(json.dumps({"verification_file": str(destination / "verification.json"), "total_frames": result["total_frames"],
                      "sample_rate": result["sample_rate"], "new_model_calls": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
