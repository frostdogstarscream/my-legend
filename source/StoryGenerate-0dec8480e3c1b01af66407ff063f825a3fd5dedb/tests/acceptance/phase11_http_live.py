"""Real HTTP processes + durable worker + PostgreSQL + actual model calls.

Run with STORY_DATABASE_URL and DRAMA_LLM_API_KEY configured. No mock fallback.
Fresh invocations create and retain an isolated schema; --resume-remix-from
reuses a passed source chain and explicitly retries its failed Remix job.
Credentials stay in child
process environments; saved HTTP/model evidence is redacted.
"""

from __future__ import annotations
import argparse, json, os, secrets, socket, subprocess, sys, time, uuid
from pathlib import Path
from datetime import datetime, timezone
import httpx
import shutil

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "4-引擎代码"))
from drama_engine.service.config import redact
from drama_engine.service.runtime import usage


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(redact(data), ensure_ascii=False, indent=2), encoding="utf-8"
    )


class LiveAcceptance:
    def __init__(self, out):
        self.out = out
        self.env = os.environ.copy()
        self.key = secrets.token_urlsafe(32)
        self.schema = "phase11_live_" + uuid.uuid4().hex[:12]
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.env.update(
            PYTHONPATH=str(ROOT / "4-引擎代码"),
            PYTHONIOENCODING="utf-8",
            STORY_DATABASE_SCHEMA=self.schema,
            STORY_API_KEYS=json.dumps(
                {self.key: {"principal_id": "phase11-acceptance", "admin": True}}
            ),
            STORY_WORKER_LEASE_SECONDS="30",
            STORY_WORKER_HEARTBEAT_SECONDS="5",
            STORY_EVIDENCE_DIR=str(out / "model_calls"),
        )
        # Redactor gets the same transient secrets, without writing an env file.
        os.environ["STORY_API_KEYS"] = self.env["STORY_API_KEYS"]
        self.client = httpx.Client(
            base_url=f"http://127.0.0.1:{self.port}",
            headers={"Authorization": "Bearer " + self.key},
            timeout=20,
        )
        self.api = None
        self.worker = None
        self.results = []
        self.handles = []
        self.resources = {}
        self.started = time.monotonic()

    def launch(self, kind):
        log = open(
            self.out / (kind + "_" + uuid.uuid4().hex + ".log"), "w", encoding="utf-8"
        )
        self.handles.append(log)
        args = [sys.executable, "-m", "drama_engine.service", kind]
        if kind == "serve":
            args += ["--port", str(self.port)]
        return subprocess.Popen(
            args,
            cwd=ROOT,
            env=self.env,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

    @staticmethod
    def stop(process):
        if process and process.poll() is None:
            process.terminate()
            process.wait(timeout=20)

    def start_api(self):
        self.api = self.launch("serve")
        for _ in range(60):
            if self.api.poll() is not None:
                raise RuntimeError("HTTP_SERVICE_START_FAILED; inspect local log")
            try:
                if self.client.get("/health").status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(0.5)
        raise TimeoutError("HTTP_SERVICE_START_TIMEOUT")

    def call(self, folder, label, method, path, body=None, expected=200):
        start = time.monotonic()
        response = self.client.request(method, path, json=body)
        data = (
            response.json()
            if "application/json" in response.headers.get("content-type", "")
            else response.text
        )
        save(
            folder / (label + ".json"),
            {
                "method": method,
                "path": path,
                "request": body,
                "status_code": response.status_code,
                "response": data,
                "request_seconds": time.monotonic() - start,
                "correlation_id": response.headers.get("x-request-id"),
            },
        )
        if response.status_code != expected:
            raise AssertionError(f"{label}: HTTP {response.status_code}: {data}")
        return data

    def wait(self, folder, kind, resource_id, label):
        start = time.monotonic()
        while time.monotonic() - start < 1800:
            response = self.client.get(f"/v1/{kind}/{resource_id}")
            response.raise_for_status()
            data = response.json()
            if data["status"] in ("SUCCEEDED", "FAILED", "CANCELLED"):
                save(folder / (label + ".json"), data)
                return data
            if self.worker and self.worker.poll() is not None:
                raise RuntimeError("WORKER_PROCESS_STOPPED")
            time.sleep(1)
        raise TimeoutError("ASYNC_OPERATION_TIMEOUT; preserve pending cost evidence")

    def operation(self, label, value, revise_plan=False, initial_task=None):
        folder = self.out / label
        folder.mkdir(exist_ok=True)
        start = time.monotonic()
        record = {
            "operation": label,
            "status": "running",
            "request_id": value["request_id"],
        }
        save(folder / "request.json", value)
        try:
            task = initial_task or self.call(
                folder, "submit", "POST", "/v1/plans", value, 202
            )
            self.resources[task["task_id"]] = {"operation": label, "phase": "plan"}
            same = self.call(
                folder, "submit_idempotent", "POST", "/v1/plans", value, 202
            )
            assert same["task_id"] == task["task_id"]
            finished = self.wait(folder, "tasks", task["task_id"], "planning_task")
            if finished["status"] != "SUCCEEDED":
                raise AssertionError("PLAN_TASK_FAILED: " + str(finished))
            plan = self.call(
                folder, "plan", "GET", "/v1/plans/" + finished["result"]["plan_id"]
            )
            self.call(
                folder,
                "unapproved_generation",
                "POST",
                "/v1/plans/" + plan["plan_id"] + "/generate",
                expected=409,
            )
            if revise_plan:
                change = {
                    "expected_revision": plan["plan_revision"],
                    "feedback": "保留固定故障原因、人物身份、两集120秒及未兑现三声铃，增强共同核对设备的具体行动。",
                    "idempotency_key": uuid.uuid4().hex,
                }
                revision = self.call(
                    folder,
                    "revision_submit",
                    "POST",
                    "/v1/plans/" + plan["plan_id"] + "/revisions",
                    change,
                    202,
                )
                self.resources[revision["task_id"]] = {
                    "operation": label,
                    "phase": "plan_revision",
                }
                revised = self.wait(
                    folder, "tasks", revision["task_id"], "revision_task"
                )
                assert revised["status"] == "SUCCEEDED", revised
                plan = self.call(
                    folder, "revised_plan", "GET", "/v1/plans/" + plan["plan_id"]
                )
                assert plan["plan_revision"] == 2
            approval = {
                "expected_revision": plan["plan_revision"],
                "fingerprint": plan["plan_fingerprint"],
                "idempotency_key": uuid.uuid4().hex,
            }
            job = self.call(
                folder,
                "approval",
                "POST",
                "/v1/plans/" + plan["plan_id"] + "/approve",
                approval,
                202,
            )
            again = self.call(
                folder,
                "approval_idempotent",
                "POST",
                "/v1/plans/" + plan["plan_id"] + "/approve",
                approval,
                202,
            )
            assert again["job_id"] == job["job_id"]
            self.resources[job["job_id"]] = {"operation": label, "phase": "generation"}
            final = self.wait(folder, "jobs", job["job_id"], "generation_attempt1")
            if final["status"] == "FAILED":
                # Explicit existing API retry, not a hidden/mock fallback. Original evidence retained.
                self.call(
                    folder,
                    "retry",
                    "POST",
                    "/v1/jobs/" + job["job_id"] + "/retry",
                    expected=202,
                )
                final = self.wait(folder, "jobs", job["job_id"], "generation_attempt2")
            if final["status"] != "SUCCEEDED":
                raise AssertionError("GENERATION_FAILED: " + str(final))
            vid = final["result_story_version_id"]
            delivery = self.call(folder, "delivery", "GET", "/v1/story-versions/" + vid)
            exported = self.call(
                folder, "export", "GET", "/v1/story-versions/" + vid + "/export"
            )
            assert exported == delivery
            self.call(
                folder,
                "script",
                "GET",
                "/v1/story-versions/" + vid + "/export?format=script",
            )
            self.call(
                folder,
                "characters",
                "GET",
                "/v1/story-versions/" + vid + "/export?format=characters",
            )
            manifest = self.call(
                folder,
                "render",
                "GET",
                "/v1/story-versions/" + vid + "/render-manifest",
            )
            assert len(manifest["tasks"]) == sum(
                len(s["utterances"]) for e in delivery["episodes"] for s in e["scenes"]
            )
            simulated = self.call(
                folder,
                "simulation",
                "POST",
                "/v1/story-versions/" + vid + "/simulate-tts",
            )
            assert (
                simulated["simulation_only"] is True
                and simulated["audio_state"] == "NOT_SYNTHESIZED"
                and simulated["playback_state"] == "NOT_REPORTED"
            )
            record.update(
                status="passed",
                job_id=job["job_id"],
                plan_id=plan["plan_id"],
                story_version_id=vid,
                story_id=delivery["story_id"],
                episodes=len(delivery["episodes"]),
            )
            return delivery
        except Exception as exc:
            record.update(
                status="failed", error_type=type(exc).__name__, error=str(exc)
            )
            return None
        finally:
            record["wall_seconds"] = round(time.monotonic() - start, 3)
            self.results.append(record)
            save(folder / "result.json", record)
            print(json.dumps(record, ensure_ascii=False), flush=True)

    def run(self):
        self.out.mkdir(parents=True, exist_ok=True)
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
        save(
            self.out / "manifest.json",
            {
                "implementation_sha": sha,
                "tracked_dirty": bool(
                    subprocess.check_output(
                        ["git", "status", "--porcelain", "--untracked-files=no"],
                        cwd=ROOT,
                        text=True,
                    ).strip()
                ),
                "schema": self.schema,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "real_model": True,
                "mock_fallback": False,
                "transport": "real localhost HTTP + independent worker processes",
            },
        )
        result = subprocess.run(
            [sys.executable, "-m", "drama_engine.service", "migrate"],
            cwd=ROOT,
            env=self.env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if result.returncode:
            raise RuntimeError("MIGRATION_FAILED: " + redact(result.stderr))
        self.start_api()
        catalog = json.loads(
            (ROOT / "examples/phase11/characters.json").read_text(encoding="utf-8")
        )
        self.call(self.out, "catalog_import", "POST", "/v1/characters/import", catalog)
        bindings = [
            {
                "character_id": "linlan",
                "role_id": "repairer",
                "performer_id": "voice-linlan",
            },
            {
                "character_id": "zhouye",
                "role_id": "host",
                "performer_id": "voice-zhouye",
            },
        ]

        def req(intent, instruction, source=None, **extra):
            r = {
                "intent": intent,
                "request_id": uuid.uuid4().hex,
                "idempotency_key": uuid.uuid4().hex,
                "user_instruction": instruction,
                **extra,
            }
            if source:
                r["source_selector"] = {
                    "type": "by_id",
                    "story_version_id": source["story_version_id"],
                }
            return r

        value = req(
            "create",
            "林岚是广播站维修员，周野是社区广播主持人，两人修复老街广播站。旧电源接头进水造成故障，必须明确排除人为破坏。两人从猜疑到信任合作，约定广播恢复验收后敲三声铃报平安，但本次两集不得兑现。保留可供续写的未完成维修验收，不新增无关阴谋或倒计时。",
            creation_preferences={
                "story_mode": "general",
                "genre": "温情",
                "target_episodes": 2,
                "target_duration_sec": 120,
                "ending_preference": "open",
            },
            characters={
                "selected_character_ids": ["linlan", "zhouye"],
                "role_bindings": bindings,
            },
        )
        queued = self.call(
            self.out / "create",
            "submit_before_restart",
            "POST",
            "/v1/plans",
            value,
            202,
        )
        self.stop(self.api)
        self.start_api()
        restored = self.call(
            self.out, "restart_queued_task", "GET", "/v1/tasks/" + queued["task_id"]
        )
        assert restored["status"] == "QUEUED"
        self.worker = self.launch("worker")
        created = self.operation("create", value, revise_plan=True, initial_task=queued)
        if not created:
            return
        assert len(created["episodes"]) == 2 and any(
            "三声铃" in t for t in created["continuity_state"]["unresolved_threads"]
        )
        # Stop only idle processes; no hidden billable regeneration during this restart check.
        self.stop(self.worker)
        self.stop(self.api)
        self.start_api()
        assert (
            self.call(
                self.out,
                "restart_created_version",
                "GET",
                "/v1/story-versions/" + created["story_version_id"],
            )
            == created
        )
        self.worker = self.launch("worker")
        e = created["episodes"][0]
        scene = next(
            s
            for s in e["scenes"]
            if any(u["kind"] == "dialogue" for u in s["utterances"])
        )
        revised = self.operation(
            "revise",
            req(
                "revise",
                "只修改指定场景中的决策方式：林岚先请周野共同核对设备状态，再决定下一步。保留原有身份、故障原因和行动结果，不改其他场景，不提前兑现三声铃。",
                created,
                edit={
                    "scope": "scene",
                    "target": {
                        "episode_id": e["episode_id"],
                        "scene_id": scene["scene_id"],
                    },
                    "instruction": "先请周野共同核对设备状态再决定下一步，保留已发生事实。",
                    "preserve": {"identity": True, "facts": True},
                    "downstream_policy": "branch_from_here",
                },
            ),
        )
        if not revised:
            return
        assert revised["episodes"][1:] == created["episodes"][1:]
        assert [
            s
            for s in revised["episodes"][0]["scenes"]
            if s["scene_id"] != scene["scene_id"]
        ] == [s for s in e["scenes"] if s["scene_id"] != scene["scene_id"]]
        continued = self.operation(
            "continue",
            req(
                "continue",
                "新增两集。第一新增集承接来源尚未完成的维修任务，完成带负载试音，发现音量不稳定并登记待校准；第二新增集承接该结果完成校准，最后真正敲三声铃回收报平安的约定。继承人物职业、雨水故障事实和信任合作关系。不要随意增加精确时刻。",
                revised,
                creation_preferences={
                    "target_episodes": 2,
                    "ending_preference": "closed",
                },
                client_context={
                    "operation_requirements": {
                        "bell": "结尾必须实际呈现已经发生的三声铃响（叮、叮、叮或明确三声铃声），不能仅是角色声称要敲或已敲。"
                    },
                    "evidence_kinds": {
                        "constraint:bell": ["sfx", "action", "narration"]
                    },
                },
            ),
        )
        if not continued:
            return
        assert (
            len(continued["episodes"]) == 4
            and continued["episodes"][:2] == revised["episodes"]
        )
        assert not any(
            "三声铃" in t for t in continued["continuity_state"]["unresolved_threads"]
        )
        remix = self.operation(
            "remix",
            req(
                "remix",
                "创作两集平行版本，保留维修员林岚、主持人周野与老街广播站。设备没有进水，周野本晚因害怕告别故意断开电源并隐瞒。林岚同晚查明事实，周野同晚公开道歉，两人约定合作整改，不以修好设备敲三声铃收束。两集均同一晚，广播站计划明天关停，不能把本晚故障写成昨天。",
                continued,
                characters={
                    "selected_character_ids": ["linlan", "zhouye"],
                    "role_bindings": bindings,
                },
                creation_preferences={
                    "story_mode": "general",
                    "genre": "温情",
                    "target_episodes": 2,
                    "target_duration_sec": 120,
                    "ending_preference": "closed",
                },
                remix={
                    "instruction": "故障改为周野本晚人为断电并隐瞒，结局改为公开道歉和合作整改。",
                    "retain_elements": [
                        "林岚维修员/周野主持人的固定身份",
                        "老街广播站背景",
                    ],
                    "transform_elements": [
                        "设备故障由周野主动断电造成，不能是进水",
                        "公开道歉并约定合作整改，不能三声铃报平安结尾",
                    ],
                    "relation_to_source": "parallel_world",
                },
                client_context={
                    "operation_requirements": {
                        "calendar": "两集同一晚发生本晚断电、调查和道歉，明天关停；不得写昨天发生本晚故障。"
                    }
                },
            ),
        )
        if not remix:
            return
        assert (
            remix["story_id"] != continued["story_id"]
            and remix["parent_story_version_id"] == continued["story_version_id"]
        )
        lineage = self.call(
            self.out,
            "lineage",
            "GET",
            "/v1/story-versions/" + remix["story_version_id"] + "/lineage",
        )
        assert len(lineage["versions"]) == 4
        self.call(
            self.out,
            "story_versions",
            "GET",
            "/v1/stories/" + created["story_id"] + "/versions",
        )
        save(
            self.out / "restart_evidence.json",
            {
                "api_restart_preserved_queued_plan": True,
                "api_and_worker_restart_preserved_created_delivery": True,
                "new_sessions_and_full_exports_checked_each_operation": True,
            },
        )

    def resume_remix(self, previous):
        """Rerun only the affected operation; prior real evidence remains explicit."""
        from drama_engine.workflow.schemas import StoryDelivery
        from drama_engine.workflow.timeline import timeline_errors

        prior = json.loads((previous / "summary.json").read_text(encoding="utf-8"))
        manifest = json.loads((previous / "manifest.json").read_text(encoding="utf-8"))
        self.schema = manifest["schema"]
        self.env["STORY_DATABASE_SCHEMA"] = self.schema
        self.out.mkdir(parents=True, exist_ok=True)
        shutil.copytree(previous / "model_calls", self.out / "model_calls")
        self.resources = prior["resources"]
        self.resources = {
            key: {"operation": value["operation"], "phase": value["phase"]}
            for key, value in self.resources.items()
        }
        save(
            self.out / "manifest.json",
            {
                "implementation_sha": subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
                ).strip(),
                "schema": self.schema,
                "real_model": True,
                "mock_fallback": False,
                "resumed_from": str(previous.relative_to(ROOT)),
                "inherited_calls_are_original_evidence_not_new_billed_calls": True,
            },
        )
        self.start_api()
        validations = []
        for operation in ("create", "revise", "continue"):
            shutil.copytree(previous / operation, self.out / operation)
            original = json.loads(
                (previous / operation / "delivery.json").read_text(encoding="utf-8")
            )["response"]
            read = self.call(
                self.out,
                "revalidate_" + operation,
                "GET",
                "/v1/story-versions/" + original["story_version_id"],
            )
            assert read == original
            delivery = StoryDelivery.model_validate(read)
            assert not timeline_errors(
                delivery.quality_report.timeline_claims, delivery.episodes
            )
            record = next(r for r in prior["results"] if r["operation"] == operation)
            assert record["status"] == "passed"
            self.results.append(
                {
                    **record,
                    "reused_real_evidence_from": str(previous.relative_to(ROOT)),
                    "fresh_model_calls_in_resume": 0,
                }
            )
            validations.append(
                {
                    "operation": operation,
                    "unchanged_database_delivery": True,
                    "new_timeline_gate_passed": True,
                    "story_version_id": delivery.story_version_id,
                }
            )
        save(self.out / "prior_stage_revalidation.json", validations)
        folder = self.out / "remix"
        folder.mkdir()
        for file in (previous / "remix").glob("*.json"):
            shutil.copy2(
                file,
                folder
                / ("prior_result.json" if file.name == "result.json" else file.name),
            )
        approval = json.loads((folder / "approval.json").read_text(encoding="utf-8"))[
            "response"
        ]
        start = time.monotonic()
        self.results.append(
            {"operation": "remix", "status": "failed", "reason": "Resume not completed"}
        )
        self.call(
            folder,
            "retry_after_parser_fix",
            "POST",
            "/v1/jobs/" + approval["job_id"] + "/retry",
            expected=202,
        )
        self.stop(self.api)
        self.start_api()
        assert (
            self.call(
                folder,
                "queued_job_after_restart",
                "GET",
                "/v1/jobs/" + approval["job_id"],
            )["status"]
            == "QUEUED"
        )
        self.worker = self.launch("worker")
        job = self.wait(folder, "jobs", approval["job_id"], "generation_attempt3")
        assert job["status"] == "SUCCEEDED", job["error_message"]
        version_id = job["result_story_version_id"]
        delivery = self.call(
            folder, "delivery", "GET", "/v1/story-versions/" + version_id
        )
        exported = self.call(
            folder,
            "export",
            "GET",
            "/v1/story-versions/" + version_id + "/export?format=json",
        )
        assert exported == delivery
        for label, route in (
            ("script", "/export?format=script"),
            ("characters", "/export?format=characters"),
            ("render", "/render-manifest"),
        ):
            self.call(folder, label, "GET", "/v1/story-versions/" + version_id + route)
        simulated = self.call(
            folder,
            "simulation",
            "POST",
            "/v1/story-versions/" + version_id + "/simulate-tts",
        )
        assert (
            simulated["simulation_only"]
            and simulated["audio_state"] == "NOT_SYNTHESIZED"
        )
        lineage = self.call(
            self.out, "lineage", "GET", "/v1/story-versions/" + version_id + "/lineage"
        )
        assert len(lineage["versions"]) == 4
        source_story_id = next(
            r["story_id"] for r in self.results if r["operation"] == "continue"
        )
        self.call(
            self.out,
            "story_versions",
            "GET",
            "/v1/stories/" + source_story_id + "/versions",
        )
        record = {
            "operation": "remix",
            "status": "passed",
            "job_id": job["job_id"],
            "plan_id": approval["approved_plan_id"],
            "story_version_id": version_id,
            "story_id": delivery["story_id"],
            "episodes": len(delivery["episodes"]),
            "wall_seconds": round(time.monotonic() - start, 3),
            "attempt": 3,
        }
        self.results[-1] = record
        save(folder / "result.json", record)
        save(
            self.out / "restart_evidence.json",
            {
                "api_restart_preserved_queued_generation": True,
                "new_api_reads_preserved_all_three_prior_versions": True,
            },
        )
        print(json.dumps(record, ensure_ascii=False), flush=True)

    def close(self):
        self.stop(self.worker)
        self.stop(self.api)
        self.client.close()
        for log in self.handles:
            log.close()
        captures = [
            json.loads(p.read_text(encoding="utf-8"))
            for p in (self.out / "model_calls").rglob("call_*.json")
        ]
        calls = [c for cap in captures for c in cap.get("calls", [])]
        summaries = {}
        for resource, info in self.resources.items():
            records = [
                c
                for p in (self.out / "model_calls" / resource).rglob("call_*.json")
                for c in json.loads(p.read_text(encoding="utf-8")).get("calls", [])
            ]
            summaries[resource] = {**info, **usage(records)}
        save(
            self.out / "summary.json",
            {
                "results": self.results,
                "resources": summaries,
                "usage": usage(calls),
                "pending_response_usage_unknown": sum(
                    cap["status"] == "pending" for cap in captures
                ),
                "wall_seconds": time.monotonic() - self.started,
            },
        )
        # Private API key is never in a saved request or subprocess log.
        for path in self.out.rglob("*"):
            if (
                path.is_file()
                and path.suffix != ".log"
                and self.key.encode() in path.read_bytes()
            ):
                raise AssertionError("SECRET_FOUND_IN_EVIDENCE")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume-remix-from", type=Path)
    args = parser.parse_args()
    if not os.environ.get("STORY_DATABASE_URL") or not (
        os.environ.get("DRAMA_LLM_API_KEY") or os.environ.get("STORY_LLM_API_KEY")
    ):
        raise RuntimeError("REAL_PROVIDER_AND_DATABASE_ENV_REQUIRED")
    runner = LiveAcceptance(args.output.resolve())
    try:
        if args.resume_remix_from:
            runner.resume_remix(args.resume_remix_from.resolve())
        else:
            runner.run()
    except Exception as exc:
        if runner.results and runner.results[-1]["status"] == "passed":
            runner.results[-1].update(
                status="failed",
                transport_succeeded=True,
                postcondition_error=type(exc).__name__ + ": " + str(exc),
            )
            save(runner.out / "postcondition_failure.json", runner.results[-1])
        save(
            runner.out / "run_error.json",
            {"error_type": type(exc).__name__, "error": str(exc)},
        )
        raise
    finally:
        finished = {item["operation"] for item in runner.results}
        for operation in ("create", "revise", "continue", "remix"):
            if operation not in finished:
                runner.results.append(
                    {
                        "operation": operation,
                        "status": "blocked",
                        "reason": "Previous stage did not pass acceptance",
                    }
                )
        runner.close()
    return (
        0
        if len(runner.results) == 4
        and all(r["status"] == "passed" for r in runner.results)
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
