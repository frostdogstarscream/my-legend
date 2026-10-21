from __future__ import annotations

import csv
import html
import json
import shutil
import tempfile
from pathlib import Path

from .contracts import ReviewRecord
from .io import digest, file_hash, load, save, under
from .media import mix
from .budget import CallBudget
from .instructions import policy


def stamp(frame: int, rate: int):
    ms = round(frame * 1000 / rate)
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02}.{ms % 1000:03}"


def public_tasks(tasks: list[dict], assets: dict):
    output = []
    for task in tasks:
        payload = task["payload"]
        output.append({k: task[k] for k in ("task_id", "block_id", "kind", "source_version_id", "input_sha256", "candidate_version", "adapter", "dependencies")} | {"text": payload.get("text"), "voice": payload.get("voice"), "instructions": payload.get("instructions"), "prompt": payload.get("prompt"), "model": payload["provider"].get("model"), "seed": payload.get("seed"), "execution": assets.get(task["block_id"], {}).get("raw", {}).get("origin", "not_completed"), "processing": payload["processing_defaults"] | payload.get("processing", {}), "actual_generation": assets.get(task["block_id"], {}).get("generation", {})})
        output[-1].update(instruction_weight=payload.get("instruction_weight"), instruction_max_weight=payload.get("instruction_max_weight"), render=payload.get("render"))
    return output


def preview(root: Path, revision: int, script: dict, performance: dict, tasks: list, assets: dict, timeline: dict, config: dict, failures: dict, source_record=None, call_budget=None):
    directory = root / "drafts" / f"r{revision}"
    directory.mkdir(parents=True, exist_ok=True)
    # New revision directory; same revision can be replayed idempotently before publication.
    # Input lookup paths are runtime-only. Export source media references as packaged asset paths.
    exported_script = json.loads(json.dumps(script))
    for e in exported_script["events"]:
        if e["block_id"] in assets:
            e["media"] = {"strategy": "library", "file": assets[e["block_id"]]["raw"]["file"], "sha256": assets[e["block_id"]]["raw"]["sha256"]}
    exported_script["source"]["export_lineage"] = {"canonical_script_sha256": digest(script), "transform": "Replace runtime media lookup paths with packaged asset references; source dialogue and IDs unchanged", "canonical_artifact_revision": script["artifact_revision"]}
    exported_script["artifact_revision"] = script["artifact_revision"] + ".portable-export"
    save(directory / "export_lineage.json", {"canonical_script_sha256": digest(script), "exported_script_sha256": digest(exported_script), "canonical_snapshot": "sources/" + digest(script) + ".json", "transform": exported_script["source"]["export_lineage"]})
    documents = {"script.json": exported_script, "performance.json": performance, "prompts.json": public_tasks(tasks, assets), "assets.json": assets, "timeline.json": timeline, "failures.json": failures}
    documents["call_budget.json"] = {"usage": call_budget or {}, "limits": CallBudget.limits(config.get("call_budget", {})), "instruction_policy": policy(config)}
    task_records = [{k: t[k] for k in ("schema_version", "task_id", "block_id", "kind", "source_version_id", "artifact_revision", "input_sha256", "candidate_version", "adapter", "dependencies")} | {"status": "success" if t["block_id"] in assets else failures.get(t["task_id"], {}).get("status", "missing")} for t in tasks]
    documents["tasks.json"] = task_records
    for name, data in documents.items():
        save(directory / name, data)
    with (directory / "timeline.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        fields = ["block_id", "kind", "start_frame", "end_frame", "duration_basis", "asset_id", "file"]
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(timeline["placements"])
    subtitles = ["WEBVTT", ""]
    for s in timeline["subtitles"]:
        subtitles.extend([s["block_id"], stamp(s["start_frame"], timeline["sample_rate"]) + " --> " + stamp(s["end_frame"], timeline["sample_rate"]), s["text"].replace("-->", "→"), ""])
    (directory / "subtitles.vtt").write_text("\n".join(subtitles), encoding="utf-8")
    mix_record = mix(root, timeline, config, directory / "draft.wav") if assets else None
    checks = dict(technical_passed=not failures and not any(i["severity"] == "blocking" for i in timeline["issues"]) and bool(mix_record),
                  waveform_measured=bool(mix_record), mix=mix_record, timeline_issues=timeline["issues"],
                  workflow_status="awaiting_human_review", human_acceptance="pending", publication="not_approved",
                  visual_validation=timeline["visual"]["engine_validation"], failures=failures)
    checks["capability_gaps"] = [{"block_id": block_id, **gap} for block_id, asset in assets.items() for gap in asset.get("generation", {}).get("capability_gaps", [])]
    save(directory / "technical_checks.json", checks)
    reading = [f"# {script['title']}", "", "草稿阅读稿。技术检查与人工听审独立；原始来源问题保留在 script.json。", ""]
    roles = {r["role_id"]: r["name"] for r in script["roles"]}
    for e in script["events"]:
        reading.append(f"- `{e['block_id']}` {roles.get(e['role_id'], e['kind'])}：{e['text']}")
    (directory / "reading.md").write_text("\n".join(reading), encoding="utf-8")
    cards, rows = [], []
    for p in timeline["placements"]:
        if p.get("file"):
            rel = "../../" + p["file"]
            cards.append(f"<article><h3>{html.escape(p['block_id'])} · {html.escape(p['kind'])}</h3><p>{html.escape(p['text'])}</p><audio controls preload='metadata' src='{html.escape(rel)}'></audio></article>")
        rows.append(f"<tr><td>{html.escape(p['block_id'])}</td><td>{html.escape(p['kind'])}</td><td>{p['start_frame']/timeline['sample_rate']:.3f}</td><td>{p['end_frame']/timeline['sample_rate']:.3f}</td><td>{html.escape(p['duration_basis'])}</td></tr>")
    player = "<audio controls preload='metadata' src='draft.wav'></audio>" if mix_record else "<p>缺少素材，尚无可播放混音。</p>"
    page = f"""<!doctype html><html lang='zh-CN'><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'><title>{html.escape(script['title'])} · 草稿审阅</title>
<style>body{{font:16px/1.6 system-ui;margin:auto;padding:30px;max-width:1100px;background:#f6f7fb;color:#222}}article,section{{background:white;border-radius:12px;padding:20px;margin:16px 0}}audio{{width:100%}}table{{width:100%;border-collapse:collapse}}td,th{{padding:8px;border-bottom:1px solid #ddd;text-align:left}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}a{{color:#594dc4}}</style>
<h1>{html.escape(script['title'])}</h1><p>r{revision} · 自动草稿 · 人工审阅待完成 · 视觉：{html.escape(timeline['visual']['engine_validation'])}</p>
<section><h2>完整混音</h2>{player}<p>播放混音前请暂停单项素材，避免重复播放。正式发布通过 CLI 审阅并绑定当前指纹。</p></section>
<section><h2>时间轴</h2><table><tr><th>事件</th><th>轨道</th><th>开始秒</th><th>结束秒</th><th>时长依据</th></tr>{''.join(rows)}</table></section>
<section><h2>问题与预算</h2><pre>{html.escape(json.dumps({'issues': timeline['issues'], 'budget': timeline['budget'], 'source_issues': script['issues'], 'model_capability_gaps': checks['capability_gaps']},ensure_ascii=False,indent=2))}</pre></section>
<section><h2>实际提示词</h2><a href='prompts.json'>查看逐句配音及音效提示词</a> · <a href='performance.json'>四层表演制作稿</a> · <a href='reading.md'>阅读稿</a> · <a href='call_budget.json'>调用计数与指令预算</a></section>
<h2>逐项素材</h2>{''.join(cards)}<script>document.addEventListener('play',function(e){{if(e.target.tagName==='AUDIO')document.querySelectorAll('audio').forEach(a=>{{if(a!==e.target)a.pause()}})}},true)</script></html>"""
    (directory / "preview.html").write_text(page, encoding="utf-8")
    files = {p.relative_to(root).as_posix(): file_hash(p) for p in sorted(directory.iterdir()) if p.is_file() and p.name not in {"review_bundle.json", "decision.example.json"}}
    media = {a[side]["file"]: a[side]["sha256"] for a in assets.values() for side in ("raw", "processed")}
    for asset in assets.values():
        for layer in asset["raw"].get("recipe", []):
            media[layer["file"]] = layer["sha256"]
        if asset["processing"].get("loop_parent"):
            parent = asset["processing"]["loop_parent"]
            media[parent["file"]] = parent["sha256"]
    canonical = "sources/" + digest(script) + ".json"
    source_files = {canonical: file_hash(under(root, canonical))}
    if source_record and source_record.get("snapshot_file"):
        source_files[source_record["snapshot_file"]] = source_record["file_sha256"]
    visual_source = timeline.get("visual", {}).get("evidence", {})
    if visual_source.get("snapshot_file"):
        source_files[visual_source["snapshot_file"]] = visual_source["sha256"]
    bundle = dict(source_version_id=script["source_version_id"], script_sha256=digest(script), timeline_version=timeline["timeline_version"], files=files, media=media, source_files=source_files, mix_sha256=mix_record["sha256"] if mix_record else None,
                  revision=revision, required_point_confirmations=sorted({i["confirmation_id"] for i in timeline["issues"] if i.get("confirmation_id")}), technical_passed=checks["technical_passed"])
    bundle["bundle_sha256"] = digest(bundle)
    save(directory / "review_bundle.json", bundle)
    decision = dict(decision="approve", reviewer="填写真实审阅人", scope=["dialogue", "performance", "sfx", "mix", "synchronization"], bundle_sha256=bundle["bundle_sha256"], notes=[], confirmed_points=[])
    save(directory / "decision.example.json", decision)
    return dict(directory=directory.relative_to(root).as_posix(), preview=(directory / "preview.html").relative_to(root).as_posix(), bundle=bundle, checks=checks)


def verify_bundle(root: Path, bundle: dict):
    expected = dict(bundle)
    sha = expected.pop("bundle_sha256")
    if digest(expected) != sha:
        raise ValueError("Review bundle fingerprint mismatch")
    for relative, sha in (bundle["files"] | bundle["media"] | bundle.get("source_files", {})).items():
        if file_hash(under(root, relative)) != sha:
            raise ValueError("Review artifact or media changed: " + relative)


def publish(root: Path, state: dict):
    bundle, decision = state["preview"]["bundle"], state["decision"]
    if decision.get("bundle_sha256") != bundle["bundle_sha256"]:
        raise ValueError("Approval refers to a stale review bundle")
    verify_bundle(root, bundle)
    if not bundle["technical_passed"]:
        raise ValueError("Technical failures block publication")
    reviewer = decision.get("reviewer", "").strip()
    if not reviewer or reviewer == "填写真实审阅人":
        raise ValueError("Reviewer identity required")
    scope = decision.get("scope", [])
    required_scope = {"dialogue", "performance", "sfx", "mix", "synchronization"}
    if not required_scope <= set(scope):
        raise ValueError("Listening review scope is incomplete")
    if not set(bundle["required_point_confirmations"]) <= set(decision.get("confirmed_points", [])):
        raise ValueError("Unconfirmed internal keypoints block publication")
    mode = state["config"].get("visual", {}).get("delivery_mode", "audio")
    if mode == "audiovisual" and (state["timeline"]["visual"]["engine_validation"] != "reported_executed" or "audiovisual" not in scope):
        raise ValueError("Audiovisual delivery requires execution evidence and joint review")
    identity = "release_" + digest({"bundle": bundle["bundle_sha256"], "decision": decision})[:24]
    dest = root / "releases" / identity
    if dest.exists():
        previous = load(dest / "delivery.json")
        for relative, sha in previous["files"].items():
            if file_hash(under(dest, relative)) != sha:
                raise ValueError("Published baseline was altered")
        return previous
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(dir=dest.parent, prefix=".release-"))
    try:
        draft = root / state["preview"]["directory"]
        for path in draft.iterdir():
            if path.is_file():
                shutil.copyfile(path, temp / path.name)
        shutil.copyfile(draft / "draft.wav", temp / "final.wav")
        locked = dict(state["timeline"], stage="audiovisual_locked" if mode == "audiovisual" else "audio_locked")
        save(temp / "timeline.json", locked)
        final_checks = load(temp / "technical_checks.json") | {"workflow_status": "delivered", "human_acceptance": "approved", "publication": "approved_for_bound_bundle"}
        save(temp / "technical_checks.json", final_checks)
        for relative in bundle["media"] | bundle.get("source_files", {}):
            target = temp / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(under(root, relative), target)
        # Published page uses packaged assets; it must not depend on mutable draft directories.
        page = (temp / "preview.html").read_text(encoding="utf-8").replace("../../assets/", "assets/").replace("src='draft.wav'", "src='final.wav'")
        (temp / "preview.html").write_text(page, encoding="utf-8")
        review = ReviewRecord(review_id="review_" + digest(decision)[:24], bundle_sha256=bundle["bundle_sha256"], source_version_id=state["script"]["source_version_id"], timeline_version=state["timeline"]["timeline_version"], decision="approve", reviewer=reviewer, scope=scope, notes=decision.get("notes", []), confirmed_points=decision.get("confirmed_points", [])).model_dump()
        save(temp / "review.json", review)
        manifest = dict(release_id=identity, source_version_id=bundle["source_version_id"], timeline_version=bundle["timeline_version"], bundle_sha256=bundle["bundle_sha256"], human_acceptance="approved", lock_scope=locked["stage"], visual_validation=locked["visual"]["engine_validation"], files={p.relative_to(temp).as_posix(): file_hash(p) for p in sorted(temp.rglob("*")) if p.is_file()})
        save(temp / "delivery.json", manifest)
        temp.rename(dest)
        return manifest
    finally:
        if temp.exists():
            shutil.rmtree(temp)
