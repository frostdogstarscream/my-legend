"""Offline media child. No provider configuration or credentials required."""
import argparse
from hashlib import sha256
import json
from pathlib import Path

from ..render import render_video
from ..worker import save
from .timeline import MeasuredTimeline


def run(request, root):
    values = {}
    for kind, entry in request["inputs"].items():
        raw = Path(entry["path"]).read_bytes()
        if sha256(raw).hexdigest() != entry["sha256"]:
            raise ValueError("INPUT_HASH_MISMATCH")
        if kind != "AudioMix":
            values[kind] = json.loads(raw)
    if values['MeasuredTimeline'].get('schema_version') == 'measured_timeline/1.1':
        raise ValueError('VISUAL_TIMELINE_VERSION_UNSUPPORTED')
    timeline = MeasuredTimeline.model_validate(values["MeasuredTimeline"])
    track = values["VisualTrack"]
    if track["source_version_id"] != timeline.source_version_id or track["audio_sha256"] != timeline.audio_sha256:
        raise ValueError("SOURCE_VERSION_MISMATCH")
    if timeline.audio_sha256 != request["inputs"]["AudioMix"]["sha256"] or track["total_frames"] != timeline.total_frames:
        raise ValueError("AUDIO_TIMELINE_MISMATCH")
    captions = [{"start": e.start_frame / timeline.sample_rate, "end": e.end_frame / timeline.sample_rate,
                 "text": e.text, "kind": e.kind} for e in timeline.events if e.kind in ("dialogue", "narration")]
    for relative, digest in request["asset_hashes"].items():
        asset = (Path(request["assets"]) / relative).resolve()
        if not asset.is_relative_to(Path(request["assets"]).resolve()) or sha256(asset.read_bytes()).hexdigest() != digest:
            raise ValueError("VISUAL_ASSET_CHANGED")
    result = render_video(track, request["inputs"]["AudioMix"]["path"], root / "video.mp4", request["assets"], request["ffmpeg"],
                          synthetic_audio=request["synthetic_audio"], captions=captions, watermark=None, production_style=True)
    # render_video decodes both output streams; final content quality remains separate.
    result.update(source_version_id=timeline.source_version_id, audio_sha256=timeline.audio_sha256,
                  human_review="pending", subtitle_mode="burnt_speech_and_external_timeline")
    save(root / "result.json", result)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=True)
    run(json.loads(args.request.read_text(encoding="utf-8")), args.root)


if __name__ == "__main__":
    main()
