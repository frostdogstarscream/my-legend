"""Read-only audit of downloaded acceptance artifacts, independent of API status labels."""
from hashlib import sha256
import json
import wave

from pydantic import ValidationError

from .contracts import ArtifactRef
from .playback import PlaybackManifest
from .timeline import read_timeline, arrangement_binding_matches, has_arrangement, timeline_source_rows
from .process_tree import media_environment, run_process_tree


def audit_downloads(job, view, directory, *, ffmpeg, report_directory):
    try:
        return _audit(job, view, directory, ffmpeg=ffmpeg, report_directory=report_directory)
    except (ValidationError, KeyError, TypeError, wave.Error, EOFError, json.JSONDecodeError):
        raise ValueError("ACCEPTANCE_MEDIA_SCHEMA_INVALID") from None


def _audit(job, view, directory, *, ffmpeg, report_directory):
    if (view.get("schema_version") != "production_playback_view/1.0" or
            view.get("job_id") != job["id"] or view.get("job_status") != job["status"]):
        raise ValueError("ACCEPTANCE_PLAYBACK_JOB_MISMATCH")
    refs = [ArtifactRef.model_validate(ref) for ref in job["artifacts"]]

    def local(ref):
        if ref not in refs:
            raise ValueError("ACCEPTANCE_PLAYBACK_UNREGISTERED_ARTIFACT")
        path = directory / ref.sha256
        if not path.is_file() or path.stat().st_size != ref.size_bytes or sha256(path.read_bytes()).hexdigest() != ref.sha256:
            raise ValueError("ACCEPTANCE_ARTIFACT_INVALID")
        return path

    if view.get("manifest") is None:
        if view.get("available_outputs") or any(ref.kind in ("StoryDelivery", "AudioMix", "Video") for ref in refs):
            raise ValueError("ACCEPTANCE_PLAYBACK_MISSING")
        return {"available_outputs": [], "technical_media_audit": "no_outputs", "human_quality": "pending"}
    manifest = PlaybackManifest.model_validate(view["manifest"])
    if (manifest.job_id, manifest.work_id, manifest.source_version_id) != (job["id"], job["work_id"], job["source_version_id"]):
        raise ValueError("ACCEPTANCE_PLAYBACK_SOURCE_MISMATCH")
    for name in ("script", "audio", "timeline", "video", "poster"):
        entry = getattr(manifest, name)
        if entry is not None:
            local(entry.artifact)
    outputs = [kind for kind, entry in (("script", manifest.script), ("audio", manifest.audio), ("visual", manifest.video)) if entry is not None]
    if sorted(view["available_outputs"]) != sorted(outputs):
        raise ValueError("ACCEPTANCE_OUTPUT_CLAIM_MISMATCH")
    delivery = json.loads(local(manifest.script.artifact).read_bytes())
    if delivery["story_version_id"] != manifest.source_version_id:
        raise ValueError("ACCEPTANCE_SCRIPT_VERSION_MISMATCH")
    source = [(ep["episode_id"], sc["scene_id"], line["line_id"], line["kind"], line["text"])
              for ep in delivery["episodes"] for sc in ep["scenes"] for line in sc["utterances"]]
    if not source or len({row[:3] for row in source}) != len(source):
        raise ValueError("ACCEPTANCE_SCRIPT_EVENTS_INVALID")
    report = {"available_outputs": outputs, "technical_media_audit": "passed", "source_events": len(source),
              "human_quality": "pending", "model_provenance": "requires_call_evidence"}
    if manifest.audio is not None:
        timeline = read_timeline(local(manifest.timeline.artifact).read_bytes())
        arrangement = (next((r for r in refs if r.kind == 'AudioExecutionArrangement'), None) or
                       next((r for r in refs if r.kind == 'AudioArrangement'), None))
        if not arrangement_binding_matches(timeline, arrangement):
            raise ValueError('ACCEPTANCE_ARRANGEMENT_BINDING_MISMATCH')
        if has_arrangement(timeline):
            local(arrangement)
        if (timeline.source_version_id, timeline.audio_sha256, timeline.sample_rate, timeline.total_frames) != (
                manifest.source_version_id, manifest.audio.artifact.sha256, manifest.sample_rate, manifest.total_frames):
            raise ValueError("ACCEPTANCE_TIMELINE_BINDING_MISMATCH")
        actual = timeline_source_rows(timeline)
        if actual != source:
            raise ValueError("ACCEPTANCE_TIMELINE_SOURCE_MISMATCH")
        # Current audio contract is PCM WAV. Inspect actual samples, not only RIFF headers.
        with wave.open(str(local(manifest.audio.artifact)), "rb") as stream:
            if (stream.getframerate(), stream.getnframes(), stream.getnchannels()) != (
                    timeline.sample_rate, timeline.total_frames, timeline.channels):
                raise ValueError("ACCEPTANCE_AUDIO_TIMING_MISMATCH")
            expected_bytes = stream.getnframes() * stream.getnchannels() * stream.getsampwidth()
            size = 0
            while block := stream.readframes(65536):
                size += len(block)
            if size != expected_bytes:
                raise ValueError("ACCEPTANCE_AUDIO_TRUNCATED")
        report["audio_frames"] = timeline.total_frames
        report["sample_rate"] = timeline.sample_rate
    if manifest.video is not None:
        # Fail on a missing stream or decoding error. The process cannot use network protocols.
        for name, maps in (("video", ["-map", "0:v:0", "-map", "0:a:0"]), ("poster", ["-map", "0:v:0"])):
            entry = getattr(manifest, name)
            with (report_directory / f"audit-{name}-decode.log").open("xb") as log:
                code = run_process_tree([ffmpeg, "-nostdin", "-v", "error", "-xerror", "-err_detect", "explode",
                    "-protocol_whitelist", "file,pipe", "-i", str(local(entry.artifact)), *maps, "-f", "null", "-"],
                    timeout=180, log=log, env=media_environment())
            if code:
                raise ValueError("ACCEPTANCE_MEDIA_DECODE_FAILED")
        report["video_and_audio_decode"] = "passed"
        report["poster_decode"] = "passed"
        report["audiovisual_alignment"] = "requires_anchor_evidence"
    return report
