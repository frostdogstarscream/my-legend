"""Real socket HTTP probes on an isolated restored PostgreSQL database.

No Worker is started and no model client is constructed. Archived accepted data
is restored before writes; original acceptance schema is never modified.
Requires STORY_DATABASE_URL for the local project PostgreSQL Docker container.
"""
import json
import hashlib
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import uvicorn
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "4-引擎代码"))
from drama_engine.service.api import create_app
from drama_engine.service.config import ServiceSettings


def main():
    out = ROOT / "docs/handoff/contracts"
    restore_path = out/"database-restore.json"
    subprocess.run([sys.executable, str(ROOT/"tests/acceptance/phase11_database_restore.py"),
        "--manifest", str(ROOT/"6-实测产物/phase11_live_20261015_v2/manifest.json"),
        "--output", str(restore_path)], check=True, capture_output=True)
    restore = json.loads(restore_path.read_text(encoding="utf-8"))
    url = make_url(os.environ["STORY_DATABASE_URL"]).set(database=restore["restored_database"])
    key = secrets.token_urlsafe(32)
    settings = ServiceSettings(database_url=url.render_as_string(hide_password=False),
        schema=restore["schema"], api_keys={key:{"principal_id":"phase11-acceptance", "admin":True}})
    app = create_app(settings)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(.05)
    assert server.started
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", headers={"Authorization":"Bearer "+key}, timeout=30)
    records, bodies = [], {}

    def call(method, path, body=None, expected=200):
        started = time.monotonic()
        response = client.request(method, path, json=body) if body is not None else client.request(method, path)
        value = response.json() if "application/json" in response.headers.get("content-type", "") else response.text
        label = f"{len(records)+1:02d} {method} {path}"
        records.append({"id":label, "status":response.status_code, "expected":expected,
                        "seconds":round(time.monotonic()-started, 4), "passed":response.status_code==expected})
        bodies[label] = {"request":body, "response":value}
        assert response.status_code == expected, (label, response.status_code, value)
        return value
    try:
        call("GET", "/health")
        # Execute the shipped clients against the same real socket, not just parse syntax.
        child_env = dict(os.environ, STORY_API_URL=f"http://127.0.0.1:{port}", STORY_API_KEY=key)
        clients = [[sys.executable,str(ROOT/"tools/handoff/client.py"),"send","GET","/health"]]
        shell = shutil.which("pwsh") or shutil.which("powershell")
        if shell:
            clients.append([shell,"-NoProfile","-File",str(ROOT/"docs/handoff/examples/call.ps1"),"-Method","GET","-Path","/health"])
        curl = shutil.which("curl.exe") or shutil.which("curl")
        if curl:
            clients.append([curl,"--silent","--show-error","--fail-with-body","--max-time","30",f"http://127.0.0.1:{port}/health"])
        bash = Path("C:/Program Files/Git/bin/bash.exe") if os.name == "nt" else Path("/bin/bash")
        if curl and bash.exists():
            clients.append([str(bash),str(ROOT/"docs/handoff/examples/call.sh"),"GET","/health"])
        for command in clients:
            result = subprocess.run(command,env=child_env,capture_output=True,text=True,encoding="utf-8",check=True,timeout=45)
            assert json.loads(result.stdout)["storage"] == "postgresql"
            records.append({"id":"shipped client "+Path(command[0]).name+" GET /health", "status":200,"passed":True})
        spec = call("GET", "/openapi.json")
        committed = json.loads((ROOT/"docs/phase11/openapi.json").read_text(encoding="utf-8"))
        assert spec == committed, "OPENAPI_CHANGED"
        for path in ("/docs", "/redoc", "/docs/oauth2-redirect"):
            response = client.get(path)
            assert response.status_code == 200
            records.append({"id":"GET "+path, "status":200, "passed":True})
        unauth = httpx.get(f"http://127.0.0.1:{port}/v1/stories")
        assert unauth.status_code == 401
        records.append({"id":"anonymous GET /v1/stories", "status":401, "passed":True})
        stories = call("GET", "/v1/stories?limit=200&offset=0")
        vid = "1cf88eb73f914da89b058679166830b6"
        delivery = call("GET", f"/v1/story-versions/{vid}")
        call("GET", f"/v1/stories/{delivery['story_id']}/versions")
        call("GET", f"/v1/story-versions/{vid}/lineage")
        for form in ("json", "script", "characters"):
            exported = call("GET", f"/v1/story-versions/{vid}/export?format={form}")
            if form == "json":
                assert exported == delivery
        manifest = call("GET", f"/v1/story-versions/{vid}/render-manifest")
        simulated = call("POST", f"/v1/story-versions/{vid}/simulate-tts")
        assert simulated["audio_state"] == "NOT_SYNTHESIZED"
        assert simulated["speech_count"] + simulated["cue_count"] == len(manifest["tasks"])
        call("GET", "/v1/characters?limit=50&offset=0")
        call("GET", "/v1/characters/linlan")
        call("GET", "/v1/characters/linlan?version_number=1")
        call("GET", "/v1/characters/linlan/versions")
        runtime = call("GET", "/v1/characters/linlan/runtime")
        revision = max([r["revision"] for r in runtime], default=0)
        call("PATCH", "/v1/characters/linlan/runtime", {"expected_revision":revision, "patch":{"dialogue_style":"联调隔离库测试"}})
        chars = json.loads((ROOT/"docs/handoff/examples/forest.characters.json").read_text(encoding="utf-8"))
        call("POST", "/v1/characters/import", chars)
        archived = ROOT/"6-实测产物/phase11_live_20261015_v3/create"
        plan_id = json.loads((archived/"revised_plan.json").read_text(encoding="utf-8"))["response"]["plan_id"]
        call("GET", f"/v1/plans/{plan_id}")
        call("GET", f"/v1/plans/{plan_id}/revisions")
        approval = json.loads((archived/"approval.json").read_text(encoding="utf-8"))["request"]
        job = call("POST", f"/v1/plans/{plan_id}/approve", approval, expected=202)
        call("POST", f"/v1/plans/{plan_id}/generate", expected=202)
        call("GET", f"/v1/jobs/{job['job_id']}")
        call("POST", f"/v1/jobs/{job['job_id']}/cancel", expected=409)
        call("POST", f"/v1/jobs/{job['job_id']}/retry", expected=409)
        call("POST", f"/v1/plans/{plan_id}/revisions", {"expected_revision":1, "feedback":"stale probe", "idempotency_key":secrets.token_hex(16)}, expected=409)
        request = json.loads((ROOT/"docs/handoff/examples/forest.create.json").read_text(encoding="utf-8"))
        request.update(request_id=secrets.token_hex(16), idempotency_key=secrets.token_hex(16))
        task = call("POST", "/v1/plans", request, expected=202)
        duplicate = call("POST", "/v1/plans", request, expected=202)
        assert duplicate["task_id"] == task["task_id"]
        request["user_instruction"] += " conflict"
        call("POST", "/v1/plans", request, expected=409)
        call("GET", f"/v1/tasks/{task['task_id']}")
        call("POST", f"/v1/tasks/{task['task_id']}/cancel")
        call("POST", f"/v1/tasks/{task['task_id']}/retry", expected=409)
        # New API instance and connection pool: prove persistence without a Worker.
        client.close()
        server.should_exit = True
        thread.join(10)
        restart_engine = settings.engine()
        from sqlalchemy.orm import Session
        from drama_engine.service.runtime import PlanningQueue
        with Session(restart_engine) as session:
            assert PlanningQueue(session).get(task["task_id"], "phase11-acceptance").status == "CANCELLED"
        restart_engine.dispose()
        records.append({"id":"fresh session after API shutdown", "status":"CANCELLED persisted", "passed":True})
    finally:
        client.close()
        server.should_exit = True
        thread.join(10)
        app.state.engine.dispose()
    # Exclude bulky duplicate data and anonymous schema/HTML; all endpoint bodies retained otherwise.
    bodies.pop(next(k for k in bodies if k.endswith("/openapi.json")))
    # Link byte-equivalent archived JSON instead of duplicating megabytes of stories.
    def digest(value):
        return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    archived_values = {}
    for file in (ROOT/"6-实测产物/phase11_live_20261015_v3").glob("*/*.json"):
        value = json.loads(file.read_text(encoding="utf-8"))
        if isinstance(value, dict) and "response" in value:
            archived_values[digest(value["response"])] = {"archive_path":file.relative_to(ROOT).as_posix(), "json_pointer":"/response", "canonical_sha256":digest(value["response"])}
    def compact(value):
        if len(json.dumps(value, ensure_ascii=False)) > 3000 and digest(value) in archived_values:
            return {"response_reference":archived_values[digest(value)]}
        if isinstance(value, list):
            return [compact(item) for item in value]
        return value
    for item in bodies.values():
        item["response"] = compact(item["response"])
    (out/"http-responses.json").write_text(json.dumps(bodies, ensure_ascii=False, indent=2), encoding="utf-8")
    record = {"transport":"real loopback HTTP / uvicorn / PostgreSQL16 restored isolated database",
              "model_calls":0, "worker_started":False, "original_acceptance_data_modified":False,
              "openapi_exact_match":True, "checks":records, "passed":all(r["passed"] for r in records),
              "not_executed":"No new model Plan/Revision/Generation; positive model results remain historical evidence. No independent novice human trial."}
    (out/"http-probe.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Real HTTP checks passed: {len(records)}; model calls: 0")


if __name__ == "__main__":
    main()
