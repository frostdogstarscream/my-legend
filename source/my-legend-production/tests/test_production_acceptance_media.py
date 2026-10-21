"""Downloaded media audit: fixtures are synthetic, never model-generation evidence."""
from hashlib import sha256
import io
import json
from pathlib import Path
import subprocess
import wave

import pytest

from my_legend_production.production.acceptance_media import audit_downloads
from my_legend_production.production.process_tree import media_environment


@pytest.fixture
def bundle(tmp_path):
    job = dict(id="job", work_id="work", source_version_id="v", status="succeeded", artifacts=[])
    manifest = dict(job_id="job", work_id="work", source_version_id="v")
    view = dict(schema_version="production_playback_view/1.0", job_id="job", job_status="succeeded",
                available_outputs=["script", "audio"], manifest=manifest)

    def put(name, kind, data, mime="application/json"):
        raw = data if isinstance(data, bytes) else json.dumps(data).encode()
        ref = dict(kind=kind, sha256=sha256(raw).hexdigest(), size_bytes=len(raw), source_version_id="v", media_type=mime)
        (tmp_path / ref["sha256"]).write_bytes(raw)
        job["artifacts"] = [r for r in job["artifacts"] if r["kind"] != kind] + [ref]
        manifest[name] = dict(artifact=ref, content_path=f"/api/v1/production-jobs/job/artifacts/{ref['sha256']}")
        return ref

    put("script", "StoryDelivery", dict(story_version_id="v", episodes=[dict(episode_id="ep", scenes=[dict(scene_id="sc",
        utterances=[dict(line_id="line", kind="dialogue", text="合成测试")])])]))
    data = io.BytesIO()
    with wave.open(data, "wb") as stream:
        stream.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        stream.writeframes(b"\0\0" * 48000)
    audio = put("audio", "AudioMix", data.getvalue(), "audio/wav")
    timeline = dict(source_version_id="v", sample_rate=48000, total_frames=48000, channels=1,
        audio_sha256=audio["sha256"], events=[dict(sequence=1, episode_id="ep", scene_id="sc", line_id="line",
        kind="dialogue", text="合成测试", start_frame=0, end_frame=48000, duration_basis="measured_audio", source_audio_sha256="a"*64)])
    put("timeline", "MeasuredTimeline", timeline)
    manifest.update(sample_rate=48000, total_frames=48000, duration_seconds=1.0)
    return job, view, put, timeline, tmp_path


def audit(bundle, ffmpeg=None):
    job, view, _, _, root = bundle
    return audit_downloads(job, view, root, ffmpeg=ffmpeg, report_directory=root)


def test_real_pcm_frames_and_exact_event_sources_are_audited(bundle):
    report = audit(bundle)
    assert report["audio_frames"] == 48000 and report["source_events"] == 1
    assert report["human_quality"] == "pending"
    assert report["model_provenance"] == "requires_call_evidence"


@pytest.mark.parametrize("fault,code", [("version", "SOURCE_MISMATCH"), ("claim", "OUTPUT_CLAIM_MISMATCH"),
    ("unregistered", "UNREGISTERED_ARTIFACT"), ("text", "TIMELINE_SOURCE_MISMATCH"),
    ("duration", "AUDIO_TIMING_MISMATCH"), ("truncated", "AUDIO_TRUNCATED"), ("missing_manifest", "PLAYBACK_MISSING")])
def test_api_success_and_valid_hashes_do_not_hide_bad_media(bundle, fault, code):
    job, view, put, timeline, root = bundle
    if fault == "version":
        job["source_version_id"] = "another-version"
    elif fault == "claim":
        view["available_outputs"].append("visual")
    elif fault == "unregistered":
        job["artifacts"] = [r for r in job["artifacts"] if r["kind"] != "AudioMix"]
    elif fault == "text":
        timeline["events"][0]["text"] = "不同台词"
        put("timeline", "MeasuredTimeline", timeline)
    elif fault == "duration":
        timeline["total_frames"] = 96000
        put("timeline", "MeasuredTimeline", timeline)
        view["manifest"].update(total_frames=96000, duration_seconds=2.0)
    elif fault == "truncated":
        raw = (root / view["manifest"]["audio"]["artifact"]["sha256"]).read_bytes()[:-200]
        ref = put("audio", "AudioMix", raw, "audio/wav")
        timeline["audio_sha256"] = ref["sha256"]
        put("timeline", "MeasuredTimeline", timeline)
    elif fault == "missing_manifest":
        view["manifest"] = None
    with pytest.raises(ValueError, match=code):
        audit(bundle)


@pytest.mark.parametrize("with_audio", [True, False])
def test_actual_video_decode_requires_both_streams(bundle, with_audio):
    job, view, put, _, root = bundle
    workspace = Path(__file__).resolve().parents[3]
    config = json.loads((workspace / "runtime/my-legend-production/workflows.json").read_text(encoding="utf-8"))
    ffmpeg = config["ffmpeg"]
    video = root / "synthetic.mp4"
    command = [ffmpeg, "-nostdin", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=64x64:r=10:d=1"]
    if with_audio:
        command += ["-i", str(root / view["manifest"]["audio"]["artifact"]["sha256"]), "-c:a", "aac"]
    subprocess.run([*command, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", "1", str(video)],
                   check=True, timeout=30, capture_output=True, env=media_environment())
    poster = root / "poster.png"
    subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-i", str(video), "-frames:v", "1", str(poster)],
                   check=True, timeout=30, capture_output=True, env=media_environment())
    put("video", "Video", video.read_bytes(), "video/mp4")
    put("poster", "Poster", poster.read_bytes(), "image/png")
    view["manifest"]["visual_profile"] = "pixel_performance/1"
    view["available_outputs"].append("visual")
    if with_audio:
        report = audit(bundle, ffmpeg)
        assert report["video_and_audio_decode"] == "passed" and report["poster_decode"] == "passed"
        assert report["audiovisual_alignment"] == "requires_anchor_evidence"
    else:
        with pytest.raises(ValueError, match="MEDIA_DECODE_FAILED"):
            audit(bundle, ffmpeg)
