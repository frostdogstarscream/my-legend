"""Loopback-only UI fixture. No provider, database, or production API access.

Run with --evidence <new JSONL path>, then point an isolated Vite instance at
http://127.0.0.1:8810/api/v1. IDs: outline-complete and outline-incomplete.
"""
import argparse
import copy
import json
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

PLAN = dict(title="隔离界面测试：院中约定", premise="两人因约定产生误会。", theme="互相理解",
            beginning="甲进入院子。", development="两人说明原委。", climax="误会被澄清。", ending="两人约定再见。",
            episode_outlines=[dict(index=1, title="院中相遇", core_goal="澄清误会",
                major_beats=["甲入场。", "乙说明原委。", "甲离场。"], ending="约定再见。", target_duration_sec=90)],
            character_portrayals=[dict(role_id="a", story_name="甲", external_goal="获得解释",
                emotional_avoidance="不愿承认担心", speech_style="先反问再说明", relationship_stance="想保持信任")],
            fictional_voice_roles=[dict(role_id="radio", story_name="广播员", performer_id="p1",
                medium="broadcast", dramatic_purpose="提供时间线索")],
            performance_plan={"pacing": "由紧张转为舒缓", "sound_design": {"sfx": ["风声", "脚步声"]}},
            continuity_constraints=["甲从院门入场。"], change_summary=["补充约定的原因。"])
USER = dict(id="isolated-ui-fixture", displayName="隔离界面测试", avatarUrl="/static/figma/avatar.png", vipDays=0, balance=0)
SCRIPT = dict(title="隔离界面测试：局部声音重制", story_version_id="fixture-v", character_snapshot=[dict(role_id="a", canon=dict(name="甲"))],
    episodes=[dict(episode_id="ep", title="院中相遇", scenes=[dict(scene_id="sc", title="院子", utterances=[
        dict(line_id="l1", kind="dialogue", text="谢谢你等我，我把约定记错了。", speaker_role_id="a"),
        dict(line_id="l2", kind="sfx", text="院外传来轻轻的风声。"),
        dict(line_id="l3", kind="action", text="甲走向院门。"),
        dict(line_id="l4", kind="narration", text="风停了，两人约定明日再见。", narrator_id="narrator")])])])
SCRIPT_BYTES = json.dumps(SCRIPT, ensure_ascii=False).encode()
BINDING_BYTES = json.dumps(dict(bindings={"a":"longanyang_v3.1","narrator":"longsanshu_v3.1"})).encode()
def artifact(kind, raw, mime):
    return dict(kind=kind, sha256=sha256(raw).hexdigest(), size_bytes=len(raw), source_version_id="fixture-v", media_type=mime)
SCRIPT_REF = artifact("StoryDelivery", SCRIPT_BYTES, "application/json")
REFS = [SCRIPT_REF, artifact("AudioMix", b"fixture-never-play", "audio/wav"),
    artifact("MeasuredTimeline", b"{}", "application/json"), artifact("AudioCache", b"fixture-never-decode", "application/zip"),
    artifact("VoiceBindings", BINDING_BYTES, "application/json")]


def job(identifier):
    if identifier in {"audio-ready", "remade-audio"}:
        ready = identifier == "audio-ready"
        return dict(id=identifier, work_id="fixture-work", source_version_id="fixture-v" if ready else None, revision=7,
            status="succeeded" if ready else "queued", request=dict(source=dict(kind="creative", title=SCRIPT["title"]), outputs=["audio"], mode="automatic"),
            stages=[dict(name=name, status="succeeded" if ready else "queued", error_code=None, human_review="pending") for name in ("story", "audio")],
            artifacts=REFS if ready else [], error_code=None, created_at="2026-10-19T00:00:00Z", actual_calls=0, unsettled_calls=0, unknown_calls=0)
    outline = copy.deepcopy(PLAN)
    if identifier == "outline-incomplete":
        outline.pop("episode_outlines")
    stage = dict(name="story", status="needs_input", error_code=None, human_review="pending",
                 input_request=dict(plan_id="fixture-plan", revision=3, fingerprint="f" * 64, outline=outline))
    return dict(id=identifier, work_id="fixture-work", source_version_id=None, revision=7, status="needs_input",
                request=dict(source=dict(kind="creative", title=PLAN["title"]), outputs=["script"], mode="reviewed"),
                stages=[stage], artifacts=[], error_code=None, created_at="2026-10-19T00:00:00Z",
                actual_calls=0, unsettled_calls=0, unknown_calls=0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence = args.evidence.open("x", encoding="utf-8")
    approved = set()
    failed_voice_keys = set()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, data, status=200, raw=False):
            payload = data if raw else json.dumps(dict(code=0 if status == 200 else status, message="isolated UI fixture", data=data), ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1:5194")
            self.send_header("Access-Control-Allow-Headers", "Authorization,Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
            self.end_headers()
            self.wfile.write(payload)

        def do_OPTIONS(self):
            self.respond(None)

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path.removeprefix("/api/v1")
            if path == "/home":
                return self.respond(dict(user=USER, latestStory=None))
            if path == "/production-options":
                return self.respond(dict(voices=[dict(id="longanyang_v3.1",name="龙安洋 · 阳光男声"),dict(id="longwan_v3.1",name="龙婉 · 细腻女声"),dict(id="longsanshu_v3.1",name="龙三叔 · 沉稳男声")],defaults={},description="隔离界面测试",credentialConfigured=False))
            if path == "/production-jobs":
                offset = int(parse_qs(parsed.query).get("offset", [0])[0])
                result = job("outline-complete" if offset == 0 else "outline-incomplete")
                return self.respond(dict(items=[result], next_offset=1 if offset == 0 else None))
            parts = path.strip("/").split("/")
            if len(parts) >= 2 and parts[0] == "production-jobs" and parts[1] in {"outline-complete", "outline-incomplete", "audio-ready", "remade-audio"}:
                result = job(parts[1])
                if parts[1] in approved:
                    result.update(status="queued", revision=8)
                    result["stages"] = [dict(name="story", status="queued", human_review="pending", error_code=None)]
                if len(parts) == 3 and parts[2] == "playback":
                    if parts[1] == "audio-ready":
                        entries = {name:dict(artifact=ref, content_path=f"/api/v1/production-jobs/audio-ready/artifacts/{ref['sha256']}") for name, ref in zip(("script", "audio", "timeline"), REFS)}
                        return self.respond(dict(schema_version="production_playback_view/1.0", job_id=parts[1], job_status="succeeded", available_outputs=["script", "audio"],
                            manifest=dict(schema_version="production_playback/1.0", job_id=parts[1], work_id="fixture-work", source_version_id="fixture-v", sample_rate=48000, total_frames=48000, duration_seconds=1.0, **entries)))
                    return self.respond(dict(schema_version="production_playback_view/1.0", job_id=parts[1], job_status=result["status"], available_outputs=[], manifest=None))
                if len(parts) == 4 and parts[2] == "artifacts" and parts[3] == SCRIPT_REF["sha256"]:
                    return self.respond(SCRIPT_BYTES, raw=True)
                if len(parts) == 4 and parts[2] == "artifacts" and parts[3] == REFS[-1]["sha256"]:
                    return self.respond(BINDING_BYTES, raw=True)
                if len(parts) == 2:
                    return self.respond(result)
            self.respond(None, 404)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if self.path == "/api/v1/auth/local":
                return self.respond(dict(accessToken="isolated-fixture-only", expiresIn=3600, user=USER))
            # Do not retain Authorization headers or browser/session data.
            evidence.write(json.dumps(dict(method="POST", path=self.path, body=body, model_calls=0), ensure_ascii=False) + "\n")
            evidence.flush()
            if self.path == "/api/v1/production-jobs/audio-ready/remake-audio":
                if body.get("expected_revision") == 7 and body.get("regenerate") == [] and body.get("outputs") == ["audio"] and body.get("voices") == {"a":"longwan_v3.1","narrator":"longsanshu_v3.1"}:
                    key=body.get("idempotency_key")
                    if key not in failed_voice_keys:
                        failed_voice_keys.add(key)
                        return self.respond(None, 503)
                    return self.respond(job("remade-audio"))
                if body.get("expected_revision") == 7 and body.get("regenerate") == [dict(episode_id="ep", scene_id="sc", line_id="l1")] and body.get("outputs") == ["audio"] and str(body.get("idempotency_key", "")).startswith("app-"):
                    return self.respond(job("remade-audio"))
                return self.respond(None, 422)
            expected = dict(expected_revision=7, resolution=dict(decision="approve", plan_id="fixture-plan", revision=3, fingerprint="f" * 64))
            if self.path == "/api/v1/production-jobs/outline-complete/resume" and body == expected:
                approved.add("outline-complete")
                result = job("outline-complete")
                result.update(status="queued", revision=8)
                result["stages"] = [dict(name="story", status="queued", human_review="pending", error_code=None)]
                return self.respond(result)
            self.respond(None, 422)

    print("Isolated UI fixture: http://127.0.0.1:8810 ; model_calls=0", flush=True)
    try:
        HTTPServer(("127.0.0.1", 8810), Handler).serve_forever()
    finally:
        evidence.close()


if __name__ == "__main__":
    main()
