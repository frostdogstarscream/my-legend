"""Offline package/dependency evidence; does not invoke providers or alter installed environments."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import zipfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    workspace = Path(__file__).resolve().parents[2]
    root = workspace / "runtime/my-legend-production"
    lock = json.loads((root / "dependency-lock.json").read_text(encoding="utf-8"))
    for relative, digest in lock["files"].items():
        assert sha256((root / relative).read_bytes()).hexdigest() == digest, relative
    names = ["native_segment_bridge.dart", "native_segments.py", "visual_execution.py", "playback.py",
             "visual_catalog.py", "visual_plan.py", "schemas/PlaybackManifest.schema.json"]
    contents = {}
    with zipfile.ZipFile(args.wheel) as archive:
        assert archive.testzip() is None
        for name in names:
            path = "my_legend_production/production/" + name
            raw = archive.read(path)
            assert raw == (workspace / "source/my-legend-production/src" / path).read_bytes(), name
            contents[name] = sha256(raw).hexdigest()
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "wheel": str(args.wheel.resolve()),
              "wheel_sha256": sha256(args.wheel.read_bytes()).hexdigest(), "verified_package_files": contents,
              "pinned_dependency_files_unchanged": len(lock["files"]), "model_calls": 0,
              "standalone_deployment_acceptance": False, "real_media_acceptance": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
