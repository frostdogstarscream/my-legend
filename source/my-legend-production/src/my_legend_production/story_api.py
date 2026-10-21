"""Loopback prototype API. Real StoryGenerate tasks; no local story-generation substitute."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from typing import Literal
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field


CHARACTERS = [
    {"id": "legend-moyu", "name": "墨羽", "description": "勇敢好奇，重视伙伴的故事主角。", "avatarUrl": "/static/figma/avatar.png", "owned": True,
     "tags": ["勇敢", "好奇", "重情义"], "biography": "愿意主动寻找真相，也会为伙伴承担选择的代价。", "voiceDescription": "声音尚未绑定"},
    {"id": "legend-lingmo", "name": "灵墨", "description": "细心聪慧，善于倾听的故事伙伴。", "avatarUrl": "/static/figma/ai-avatar.png", "owned": True,
     "tags": ["温柔", "聪慧", "善倾听"], "biography": "留意被忽略的细节，遇到分歧时愿意沟通。", "voiceDescription": "声音尚未绑定"},
]
USER = {"id": "local-developer", "displayName": "本机创作者", "avatarUrl": "/static/figma/avatar.png", "vipDays": 0, "balance": 0}
ACTIVE = {"running", "awaiting_approval"}


def failure_message(result):
    duration = re.search(r'duration estimate (\d+)s outside .+ of (\d+)s', result.get('error_message') or '')
    if duration:
        return f'正文估算为 {duration[1]} 秒，未满足 {duration[2]} 秒目标范围。创意已保留，可重试或简化情节后重新生成大纲'
    return '生成或质量检查未通过，原始创意已保留，请查看错误标识后重试'


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Draft(Input):
    title: str = Field(min_length=1, max_length=40)
    genre: str = Field(min_length=1, max_length=40)
    idea: str = Field(min_length=1, max_length=2000)
    roleIds: list[str] = Field(default_factory=list, max_length=2)


class Approval(Input):
    revision: int = Field(ge=1)
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class Revision(Approval):
    feedback: str = Field(min_length=1, max_length=2000)


class Upstream:
    def __init__(self, base_url: str, token: str):
        if base_url.rstrip("/") != "http://127.0.0.1:8897":
            raise ValueError("Only the configured loopback StoryGenerate API is supported")
        self.client = httpx.Client(base_url=base_url, headers={"Authorization": "Bearer " + token}, timeout=20, follow_redirects=False, trust_env=False)

    def call(self, method: str, path: str, body=None):
        try:
            response = self.client.request(method, path, json=body) if body is not None else self.client.request(method, path)
        except httpx.HTTPError:
            raise HTTPException(503, "故事服务暂时无法连接，请检查 API 和 Worker") from None
        if response.status_code >= 300:
            code = "UPSTREAM_ERROR"
            try:
                candidate = response.json().get("error", {}).get("code", "")
                if re.fullmatch(r"[A-Z0-9_]{1,80}", candidate):
                    code = candidate
            except (ValueError, AttributeError, TypeError):
                pass
            status = response.status_code if response.status_code in (404, 409, 422) else 503
            message = "大纲或任务状态已变化，请重新加载" if status == 409 else "故事服务返回错误"
            raise HTTPException(status, f"{message}（{code}）")
        try:
            return response.json()
        except ValueError:
            raise HTTPException(503, "故事服务返回了无法识别的数据") from None


class Repository:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self.root = root
        self.lock = threading.RLock()
        self.db = sqlite3.connect(root / "app.sqlite", check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS objects (kind TEXT, id TEXT, value TEXT, PRIMARY KEY(kind,id))")
        self.db.execute("CREATE TABLE IF NOT EXISTS sessions (digest TEXT PRIMARY KEY, expires REAL)")
        self.db.commit()

    def put(self, kind, value):
        self.db.execute("INSERT INTO objects VALUES(?,?,?) ON CONFLICT(kind,id) DO UPDATE SET value=excluded.value", (kind, value["id"], json.dumps(value, ensure_ascii=False)))
        self.db.commit()
        return value

    def get(self, kind, key):
        row = self.db.execute("SELECT value FROM objects WHERE kind=? AND id=?", (kind, key)).fetchone()
        if not row:
            raise HTTPException(404, "没有找到对应的故事或任务")
        return json.loads(row[0])

    def stories(self):
        rows = self.db.execute("SELECT value FROM objects WHERE kind='story' ORDER BY rowid DESC").fetchall()
        return [json.loads(row[0]) for row in rows]

    def session(self):
        token = secrets.token_urlsafe(32)
        self.db.execute("INSERT INTO sessions VALUES(?,?)", (hashlib.sha256(token.encode()).hexdigest(), time.time() + 86400))
        self.db.commit()
        return {"accessToken": token, "expiresIn": 86400, "user": USER}

    def authenticate(self, request):
        auth = request.headers.get("authorization", "")
        digest = hashlib.sha256(auth.removeprefix("Bearer ").encode()).hexdigest()
        row = self.db.execute("SELECT expires FROM sessions WHERE digest=?", (digest,)).fetchone()
        if not auth.startswith("Bearer ") or not row or row[0] <= time.time():
            raise HTTPException(401, "本机会话已过期，请重新进入")
        return digest

    def artifact(self, kind, version, data):
        raw = json.dumps(data, ensure_ascii=False, sort_keys=True).encode()
        digest = hashlib.sha256(raw).hexdigest()
        directory = self.root / "artifacts"
        directory.mkdir(exist_ok=True)
        path = directory / (digest + ".json")
        if not path.exists():
            with path.open("xb") as out:
                out.write(raw)
        return {"kind": kind, "storyVersionId": version, "sha256": digest}


def chapters_from(delivery):
    roles = {p["role_id"]: p.get("story_name", p["role_id"]) for p in delivery.get("character_portrayals", [])}
    for p in delivery.get("character_snapshot", []):
        roles.setdefault(p.get("role_id") or p["character_id"], p.get("canon", {}).get("name", p["character_id"]))
    chapters = []
    labels = {"narration": "旁白", "action": "动作", "sfx": "音效", "music": "音乐"}
    for episode in delivery["episodes"]:
        paragraphs = []
        for scene in episode["scenes"]:
            paragraphs.append("【" + scene["title"] + "】")
            for line in scene["utterances"]:
                label = roles.get(line.get("speaker_role_id"), line.get("speaker_role_id") or "角色") if line["kind"] == "dialogue" else labels[line["kind"]]
                paragraphs.append(label + "：" + line["text"])
        chapters.append({"id": episode["episode_id"], "title": episode["title"], "paragraphs": paragraphs, "source": "generated"})
    if not chapters or not any(c["paragraphs"] for c in chapters):
        raise HTTPException(503, "故事服务尚未交付完整章节")
    return chapters


def create_app(root: Path | str | None = None, upstream=None):
    root = Path(root or os.environ["MY_LEGEND_STORY_RUNTIME"])
    repo = Repository(root)
    engine = upstream or Upstream("http://127.0.0.1:8897", os.environ["MY_LEGEND_STORY_TOKEN"])
    app = FastAPI(title="我的传说 · 本机故事接入", docs_url="/docs")
    app.state.repository, app.state.upstream = repo, engine
    app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:5193", "http://localhost:5193"], allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"])

    @app.middleware("http")
    async def guard(request, call_next):
        if request.url.path.startswith("/api/v1/") and request.method != "OPTIONS":
            try:
                with repo.lock:
                    if request.url.path == "/api/v1/auth/local":
                        if request.client.host not in ("127.0.0.1", "::1", "testclient"):
                            raise HTTPException(403, "本机联调入口仅允许本机访问")
                        origin = request.headers.get("origin")
                        if origin and origin not in ("http://127.0.0.1:5193", "http://localhost:5193"):
                            raise HTTPException(403, "请求来源不受支持")
                    else:
                        request.state.session_digest = repo.authenticate(request)
            except HTTPException as exc:
                return JSONResponse({"code": exc.status_code, "message": exc.detail, "data": None}, status_code=exc.status_code)
        response = await call_next(request)
        print(f"APP_HTTP {request.method} {request.url.path} {response.status_code}", flush=True)
        return response

    @app.exception_handler(HTTPException)
    async def error(_request, exc):
        return JSONResponse({"code": exc.status_code, "message": exc.detail, "data": None}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def invalid(_request, _exc):
        print("INPUT_VALIDATION " + json.dumps([{"loc": list(e['loc']), "type": e['type']} for e in _exc.errors()]), flush=True)
        return JSONResponse({"code": 422, "message": "输入内容不完整或格式不正确", "data": None}, status_code=422)

    def ok(data):
        return {"code": 0, "message": "ok", "data": data}

    def public_task(task):
        return {k: v for k, v in task.items() if k in {"id", "storyId", "status", "progress", "message", "phase", "plan", "errorCode", "storyVersionId"}}

    def synchronize(task):
        if task["status"] not in ACTIVE:
            return task
        if task["status"] == "awaiting_approval":
            return task
        is_body = task["phase"] == "writing"
        route = "/v1/jobs/" if is_body else "/v1/tasks/"
        result = engine.call("GET", route + task["upstreamId"])
        usage = result.get('usage') or {}
        task['usage'] = {key: usage.get(key, 0) for key in ('calls', 'input_tokens', 'output_tokens', 'unknown_usage_calls')}
        task['actualModels'] = sorted({r['actual_model'] for r in usage.get('call_records', []) if r.get('actual_model')})
        state = result["status"]
        if state in ("FAILED", "CANCELLED"):
            code = result.get("error_code") or "STORY_GENERATION_FAILED"
            if not re.fullmatch(r"[A-Z0-9_]{1,80}", code):
                code = "STORY_GENERATION_FAILED"
            task.update(status="failed" if state == "FAILED" else "cancelled", message=failure_message(result) if state == "FAILED" else "任务已取消", errorCode=code)
        elif state == "SUCCEEDED":
            if is_body:
                version = result["result_story_version_id"]
                delivery = engine.call("GET", "/v1/story-versions/" + version)
                if delivery.get("status") != "READY":
                    raise HTTPException(503, "剧本尚未通过故事服务的交付检查")
                chapters = chapters_from(delivery)
                manifest = engine.call("GET", "/v1/story-versions/" + version + "/render-manifest")
                if delivery.get('story_version_id') != version or manifest.get('story_version_id') != version:
                    raise HTTPException(503, '剧本与制作任务版本不一致，暂不交付')
                artifacts = [repo.artifact("StoryDelivery", version, delivery), repo.artifact("RenderManifest", version, manifest)]
                story = repo.get("story", task["storyId"])
                story.update(chapters=chapters, status="ready", chapter=chapters[0]["title"], storyVersionId=version, handoffArtifacts=artifacts,
                             production={"story": "ready", "audio": "not_started", "visual": "not_started", "message": "剧本已就绪；声音、视觉素材及演绎尚未制作"})
                repo.put("story", story)
                task.update(status="succeeded", progress=100, message="真实剧本已生成，可阅读章节", storyVersionId=version)
            else:
                preview = engine.call("GET", "/v1/plans/" + result["result"]["plan_id"])
                task.update(status="awaiting_approval", progress=35, message="大纲已生成，请查看并确认", plan={
                    "id": preview["plan_id"], "revision": preview["plan_revision"], "fingerprint": preview["plan_fingerprint"],
                    "content": preview["plan"], "targetDurationSec": preview["resolved_preferences"]["target_duration_sec"],
                    "targetEpisodes": preview["resolved_preferences"]["target_episodes"]})
        else:
            pct = max(0, min(100, int(result.get("progress_pct", 0))))
            task.update(progress=min(95, 40 + int(pct * .55)) if is_body else min(30, int(pct * .3)),
                        message=("正在生成正文、演绎标注与质量检查" if is_body else "正在生成故事大纲") + ("（排队中）" if state == "QUEUED" else ""))
        repo.put("task", task)
        if task["status"] in ("failed", "cancelled"):
            story = repo.get("story", task["storyId"])
            story.update(status="failed" if task["status"] == "failed" else "draft", chapter=task["message"])
            repo.put("story", story)
        return task

    @app.get("/health")
    def health():
        return {"status": "ready", "mode": "local_real_story", "upstream": engine.call("GET", "/health"), "visualProductionReady": False}

    @app.post("/api/v1/auth/local")
    def local_login():
        with repo.lock:
            return ok(repo.session())

    @app.post("/api/v1/auth/logout")
    def logout(request: Request):
        with repo.lock:
            repo.db.execute("DELETE FROM sessions WHERE digest=?", (request.state.session_digest,))
            repo.db.commit()
        return ok(None)

    @app.get("/api/v1/home")
    def home():
        with repo.lock:
            stories = repo.stories()
            return ok({"user": USER, "latestStory": stories[0] if stories else None})

    @app.get("/api/v1/characters")
    def characters():
        return ok(CHARACTERS)

    @app.get("/api/v1/shop/products")
    def products():
        return ok([])

    @app.get("/api/v1/stories")
    def stories():
        with repo.lock:
            return ok([refresh_production(s) for s in repo.stories()])

    @app.post("/api/v1/stories")
    def create_draft(body: Draft):
        if not set(body.roleIds).issubset({c["id"] for c in CHARACTERS}):
            raise HTTPException(422, "参与角色不在当前角色目录中")
        with repo.lock:
            return ok(repo.put("story", {**body.model_dump(), "id": str(uuid4()), "status": "draft", "progress": 0, "chapter": "创意草稿", "coverUrl": "/static/figma/home-background.png", "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}))

    @app.get("/api/v1/stories/{story_id}")
    def story(story_id: str):
        with repo.lock:
            current = repo.get("story", story_id)
            if current.get("generationTaskId"):
                existing = repo.get("task", current["generationTaskId"])
                if existing["upstreamId"]:
                    synchronize(existing)
            return ok(refresh_production(repo.get("story", story_id)))

    @app.post("/api/v1/stories/{story_id}")
    def update_draft(story_id: str, body: Draft):
        with repo.lock:
            current = repo.get("story", story_id)
            if current["status"] not in ("draft", "failed"):
                if all(current.get(k) == v for k, v in body.model_dump().items()):
                    return ok(current)
                raise HTTPException(409, "已有生成任务，请先在任务页处理")
            if not set(body.roleIds).issubset({c["id"] for c in CHARACTERS}):
                raise HTTPException(422, "参与角色不受支持")
            current.update(**body.model_dump(), status="draft")
            current.pop("generationTaskId", None)
            return ok(repo.put("story", current))

    @app.post("/api/v1/stories/{story_id}/generation-tasks")
    def start(story_id: str):
        with repo.lock:
            current = repo.get("story", story_id)
            if current.get("generationTaskId"):
                existing = repo.get("task", current["generationTaskId"])
                if not existing["upstreamId"]:
                    existing = submit_plan(existing, current)
                return ok(public_task(synchronize(existing)))
            task_id = str(uuid4())
            task = {"id": task_id, "storyId": story_id, "status": "running", "phase": "planning", "progress": 0, "message": "正在准备真实大纲", "upstreamId": None}
            # Persist before sending. A timeout can be recovered with the same idempotency key.
            repo.put("task", task)
            current.update(status="generating", generationTaskId=task_id, chapter="正在准备大纲")
            repo.put("story", current)
            return ok(public_task(submit_plan(task, current)))

    def submit_plan(task, current):
        body = {
            "intent": "create", "request_id": "legend-" + task["id"], "idempotency_key": "legend-request-" + task["id"],
            "user_instruction": "故事名称：" + current["title"] + "。创作要求：" + current["idea"],
            "creation_preferences": {"genre": current["genre"], "story_mode": "general", "target_episodes": 1, "target_duration_sec": 90, "ending_preference": "closed"},
            "characters": {"selected_character_ids": current["roleIds"], "role_bindings": [
                {"character_id": cid, "role_id": cid, "performer_id": "performer-" + cid} for cid in current["roleIds"]]}}
        result = engine.call("POST", "/v1/plans", body)
        task["upstreamId"] = result["task_id"]
        return repo.put("task", task)

    @app.get("/api/v1/generation-tasks/{task_id}")
    def task(task_id: str):
        with repo.lock:
            current = repo.get("task", task_id)
            if not current["upstreamId"]:
                current = submit_plan(current, repo.get("story", current["storyId"]))
            return ok(public_task(synchronize(current)))

    @app.post("/api/v1/generation-tasks/{task_id}/approve")
    def approve(task_id: str, body: Approval):
        with repo.lock:
            current = repo.get("task", task_id)
            plan = current.get("plan")
            if not plan or (body.revision, body.fingerprint) != (plan["revision"], plan["fingerprint"]):
                raise HTTPException(409, "大纲已更新，请重新加载并确认")
            if current["phase"] == "writing":
                return ok(public_task(current))
            if current["status"] != "awaiting_approval":
                raise HTTPException(409, "当前任务尚未等待大纲确认")
            result = engine.call("POST", "/v1/plans/" + plan["id"] + "/approve", {
                "expected_revision": body.revision, "fingerprint": body.fingerprint,
                "idempotency_key": "legend-approve-" + task_id + "-" + str(body.revision)})
            current.update(phase="writing", status="running", upstreamId=result["job_id"], progress=40, message="大纲已确认，正文任务已提交")
            return ok(public_task(repo.put("task", current)))

    @app.post("/api/v1/generation-tasks/{task_id}/revise")
    def revise(task_id: str, body: Revision):
        with repo.lock:
            current = repo.get("task", task_id)
            if current["status"] != "awaiting_approval" or current["phase"] != "planning":
                raise HTTPException(409, "当前大纲无法修订")
            plan = current["plan"]
            if (body.revision, body.fingerprint) != (plan["revision"], plan["fingerprint"]):
                raise HTTPException(409, "大纲已更新，请重新加载")
            feedback_hash = hashlib.sha256(body.feedback.encode()).hexdigest()[:16]
            result = engine.call("POST", "/v1/plans/" + plan["id"] + "/revisions", {
                "expected_revision": body.revision, "feedback": body.feedback,
                "idempotency_key": f"legend-revise-{task_id}-{body.revision}-{feedback_hash}"})
            current.update(status="running", upstreamId=result["task_id"], progress=10, message="正在按反馈修订大纲")
            current.pop("plan", None)
            return ok(public_task(repo.put("task", current)))

    @app.post("/api/v1/generation-tasks/{task_id}/retry")
    def retry(task_id: str):
        with repo.lock:
            current = repo.get("task", task_id)
            if current["status"] != "failed":
                raise HTTPException(409, "仅失败任务可以重试")
            path = "/v1/jobs/" if current["phase"] == "writing" else "/v1/tasks/"
            engine.call("POST", path + current["upstreamId"] + "/retry")
            current.update(status="running", message="已请求重试", progress=40 if current["phase"] == "writing" else 0)
            current.pop("errorCode", None)
            story = repo.get("story", current["storyId"])
            story["status"] = "generating"
            repo.put("story", story)
            return ok(public_task(repo.put("task", current)))

    @app.post("/api/v1/generation-tasks/{task_id}/cancel")
    def cancel(task_id: str):
        with repo.lock:
            current = repo.get("task", task_id)
            if current["status"] == "awaiting_approval":
                current.update(status="cancelled", message="已停止本次创作，未提交正文生成")
                story = repo.get("story", current["storyId"])
                story.update(status="draft", chapter="创意草稿")
                story.pop("generationTaskId", None)
                repo.put("story", story)
            elif current["status"] == "running":
                path = "/v1/jobs/" if current["phase"] == "writing" else "/v1/tasks/"
                engine.call("POST", path + current["upstreamId"] + "/cancel")
                current["message"] = "取消已请求，等待后台确认"
            else:
                raise HTTPException(409, "当前任务不能取消")
            return ok(public_task(repo.put("task", current)))

    from .performance_api import register_routes
    refresh_production = register_routes(app, repo, ok)
    if os.environ.get('MY_LEGEND_PRODUCTION_DSN'):
        from .production.api import register_production_routes
        from .production.cli import capabilities, services
        production_repo, production_artifacts = services()
        from .production.reconciliation import ProductionReconciler
        register_production_routes(app, production_repo, production_artifacts, lambda _request: USER['id'], capabilities,
                                   ProductionReconciler(production_repo, engine))
        app.state.production_repository = production_repo
    return app


def main():
    import uvicorn
    uvicorn.run(create_app(), host="127.0.0.1", port=8895, access_log=False)


if __name__ == "__main__":
    main()
