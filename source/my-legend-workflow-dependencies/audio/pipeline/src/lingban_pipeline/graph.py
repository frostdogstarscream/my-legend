from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Any, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send, interrupt

from .adapters import QianwenAudioTTS, QianwenSFX, QianwenTTS, StructuredTextModel
from .budget import CallBudget
from .content import normalize, revise
from .delivery import preview, publish
from .contracts import PerformanceDocument, ReviewRecord
from .io import digest, file_hash, load, safe_config, safe_id, save, under
from .media import compose, loop_asset, process, store_raw
from .performance import TEMPLATE_VERSION, create_tasks, design, design_input_fingerprint, reuse_design
from .registry import CapabilityError, Registry, UnknownResult, verify_file
from .timeline import schedule
from .visual import obtain, prepare


def merge_results(left: dict, right: dict):
    result = dict(left)
    for key, value in right.items():
        if key in result and result[key] != value:
            raise ValueError("Conflicting duplicate task result: " + key)
        result[key] = value
    return result


class PrivateState(TypedDict, total=False):
    request: dict
    intermediate: dict
    output: dict


class TaskState(TypedDict, total=False):
    tasks: list
    task: dict
    config: dict
    results: Annotated[dict, merge_results]


class ProductionState(TypedDict, total=False):
    input_script: dict
    input_root: str
    config: dict
    source_record: dict
    revision: int
    script: dict
    performance: dict
    tasks: list
    voice_results: dict
    sfx_results: dict
    visual: dict
    assets: dict
    failures: dict
    timeline: dict
    preview: dict
    problems: list
    decision: dict
    impact: dict
    delivery: dict
    status: str


class Pipeline:
    def __init__(self, root: Path, *, audio_adapters=None, text_model=None, upstream=None, visual_adapter=None):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.registry = Registry(self.root)
        self.call_budget = CallBudget(self.root)
        self.batch_id = "r1"
        self.audio_adapters = audio_adapters or {}
        self.text_model, self.upstream, self.visual_adapter = text_model, upstream, visual_adapter
        self.guard, self.limits = threading.Lock(), {}
        self.connection = sqlite3.connect(self.root / "checkpoints.sqlite", check_same_thread=False)
        self.checkpointer = SqliteSaver(self.connection)
        self.graph = self.build()

    def close(self):
        self.connection.close()

    def limiter(self, adapter: str, cfg: dict):
        count = cfg.get("concurrency", 1 if adapter == "qianwen_sfx" else 2)
        key = (adapter, cfg.get("model"), count)
        if not isinstance(count, int) or count < 1:
            raise CapabilityError("Concurrency must be a positive integer")
        with self.guard:
            return self.limits.setdefault(key, threading.BoundedSemaphore(count))

    def audio_task(self, state: TaskState):
        task, config = state["task"], state["config"]
        payload, adapter_name = task["payload"], task["adapter"]
        try:
            adapter = self.audio_adapters.get(adapter_name)
            if adapter_name == "qianwen_tts":
                adapter = adapter or QianwenTTS(payload["provider"], budget=lambda: self.call_budget.reserve(self.batch_id, "speech", config.get("call_budget", {})))
            elif adapter_name == "qianwen_audio_tts":
                adapter = adapter or QianwenAudioTTS(payload["provider"], budget=lambda: self.call_budget.reserve(self.batch_id, "speech", config.get("call_budget", {})))
            elif adapter_name == "qianwen_sfx":
                adapter = adapter or QianwenSFX(payload["provider"], budget=lambda: self.call_budget.reserve(self.batch_id, "sfx", config.get("call_budget", {})))
            elif adapter_name not in {"local", "compose"} and adapter is None:
                raise CapabilityError("Unconfigured audio adapter")
            # Processing-only changes reuse the raw generation result.
            raw_identity = dict(payload)
            for key in ("processing", "processing_defaults", "keypoints", "sample_rate", "channels", "context", "instruction_weight", "instruction_max_weight", "render"):
                raw_identity.pop(key, None)
            raw_identity["provider"] = {k: v for k, v in payload["provider"].items() if k not in
                                        {"concurrency", "timeout_seconds", "download_attempts", "download_retry_delay_seconds", "retry_delay_seconds", "instruction_max_weight"}}
            raw_identity["media"] = {k: v for k, v in raw_identity["media"].items() if k not in {"processing", "keypoints", "loop_until", "render"}}
            if adapter_name == "compose":
                raw_identity["composition_rate"] = config.get("sample_rate", 48000)
                raw_identity["composition_channels"] = config.get("channels", 1)
            if adapter_name == "qianwen_tts":
                # Unsupported optional directions remain in the logical task, but a
                # design-only change must not trigger the same paid non-Instruct call.
                plan = QianwenTTS(payload["provider"]).request_plan(payload)
                raw_identity.pop("instructions", None)
                raw_identity.pop("required_controls", None)
                raw_identity["tts_request"] = {k: plan[k] for k in ("model", "text", "session")}
            elif adapter_name == "qianwen_audio_tts":
                plan = QianwenAudioTTS(payload["provider"]).request_plan(payload)
                raw_identity.pop("instructions", None)
                raw_identity.pop("required_controls", None)
                raw_identity["tts_request"] = {k: plan[k] for k in ("model", "input")}
            raw_id = "raw_" + digest({"adapter": adapter_name, "payload": raw_identity, "candidate": task["candidate_version"]})
            def action():
                if adapter_name == "local":
                    media = payload["media"]
                    p = Path(media["file"])
                    if file_hash(p) != media["sha256"]:
                        raise ValueError("Local input changed after task creation")
                    return store_raw(self.root, p) | {"origin": "library"}
                if adapter_name == "compose":
                    layers = payload["media"].get("layers", [])
                    for layer in layers:
                        if file_hash(Path(layer["file"])) != layer["sha256"]:
                            raise ValueError("Composition input changed")
                    return compose(self.root, layers, config.get("sample_rate", 48000), config.get("channels", 1)) | {"origin": "composition"}
                with self.limiter(adapter_name, payload["provider"]):
                    if not getattr(adapter, "manages_call_budget", False):
                        self.call_budget.reserve(self.batch_id, "speech" if task["kind"] == "speech" else "sfx", config.get("call_budget", {}))
                    path = self.root / "inflight" / f"{raw_id}.wav"
                    meta = adapter.generate(payload, path)
                    safe_config(meta)
                    return store_raw(self.root, path) | {"origin": "generated", **meta}
            external = bool(adapter and adapter.external)
            raw = self.registry.run(raw_id, external, action, lambda value: verify_file(self.root, value),
                                    retry_delay_seconds=payload["provider"].get("retry_delay_seconds", 2))
            raw.pop("cache_reused", None)
            asset = process(self.root, task, raw, config)
            # Result metadata is deterministic, so replay cannot silently select another asset.
            result = {"status": "success", "asset": asset, "raw_task_id": raw_id}
        except Exception as exc:
            result = dict(status="unknown" if isinstance(exc, UnknownResult) else "configuration_error" if isinstance(exc, (CapabilityError, KeyError)) else "failed", error_code=type(exc).__name__)
            if getattr(exc, "diagnostics", None):
                result["diagnostics"] = exc.diagnostics
            if "raw_id" in locals():
                result["raw_task_id"] = raw_id
        return {"results": {task["task_id"]: result}}

    def task_graph(self, kinds: set[str]):
        graph = StateGraph(TaskState)
        graph.add_node("dispatch", lambda _: {})
        graph.add_node("produce_measure", self.audio_task)
        graph.add_node("complete", lambda _: {})
        def dispatch(state):
            tasks = [t for t in state["tasks"] if t["kind"] in kinds]
            return [Send("produce_measure", {"task": task, "config": state["config"]}) for task in tasks] if tasks else "complete"
        graph.add_edge(START, "dispatch")
        graph.add_conditional_edges("dispatch", dispatch)
        graph.add_edge("produce_measure", "complete")
        graph.add_edge("complete", END)
        return graph.compile()

    @staticmethod
    def linear(nodes: list[tuple[str, Any]]):
        graph = StateGraph(PrivateState)
        previous = START
        for name, fn in nodes:
            graph.add_node(name, fn)
            graph.add_edge(previous, name)
            previous = name
        graph.add_edge(previous, END)
        return graph.compile()

    def build(self):
        def content_node(s):
            script, problems = normalize(s["request"]["input_script"], self.upstream)
            return {"intermediate": {"script": script, "problems": problems}}
        def record_content(s):
            data = s["intermediate"]
            save(self.root / "sources" / f"{digest(data['script'])}.json", data["script"])
            return {"output": data}
        content = self.linear([("normalize", content_node), ("check_references_record", record_content)])

        def design_node(s):
            request = s["request"]
            script, config = request["script"], request["config"]
            self.batch_id = "r" + str(request["revision"])
            model_config = config.get("text_model", {})
            if request.get("impact", {}).get("reuse_performance") and request.get("performance"):
                perf = reuse_design(script, config, request["performance"])
                save(self.root / "designs" / ("reused_" + digest(perf) + ".json"), perf)
            elif model_config.get("enabled"):
                reserve = lambda: self.call_budget.reserve(self.batch_id, "text", config.get("call_budget", {}))
                designer = self.text_model or StructuredTextModel(model_config | {"max_corrections": config.get("instruction_policy", {}).get("max_corrections", 2)}, budget=reserve)
                if self.text_model and not getattr(designer, "manages_call_budget", False):
                    inner = designer
                    class BudgetedDesigner:
                        def generate(self, prompt, validate):
                            reserve()
                            result = inner.generate(prompt, validate)
                            self.last_request_record = getattr(inner, "last_request_record", {})
                            return result
                    designer = BudgetedDesigner()
                def line_store(block_id, fingerprint, generate):
                    task_id = "text_line_" + fingerprint
                    def action():
                        result = generate()
                        safe_config(result)
                        path = self.root / "designs" / (task_id + ".json")
                        save(path, result)
                        return {"file": path.relative_to(self.root).as_posix(), "sha256": file_hash(path)}
                    response = self.registry.run(task_id, True, action, lambda v: verify_file(self.root, v), retry_delay_seconds=model_config.get("retry_delay_seconds", 2))
                    return load(under(self.root, response["file"])) | {"cache_reused": response["cache_reused"]}
                perf = design(script, config, designer, line_store)
                save(self.root / "designs" / ("bound_" + digest(perf) + ".json"), perf)
            else:
                perf = design(script, config)
            return {"intermediate": perf}
        def plan_node(s):
            request = s["request"]
            tasks = create_tasks(request["script"], s["intermediate"], request["config"], Path(request["input_root"]))
            return {"output": {"performance": s["intermediate"], "tasks": tasks}}
        performance = self.linear([("four_layer_design", design_node), ("adapter_task_plan", plan_node)])
        voice, sounds = self.task_graph({"speech"}), self.task_graph({"sfx", "ambience", "music"})
        visual = self.linear([("semantic_requests", lambda s: {"intermediate": prepare(s["request"]["script"], s["request"]["performance"], s["request"]["config"])}), ("capability_status", lambda s: {"output": {"visual": s["intermediate"]}})])

        def collect(s):
            request = s["request"]
            tasks = {t["task_id"]: t for t in request["tasks"]}
            results = merge_results(request.get("voice_results", {}), request.get("sfx_results", {}))
            assets, failures = {}, {}
            for task_id, task in tasks.items():
                if task["source_version_id"] != request["script"]["source_version_id"]:
                    raise ValueError("Branch task uses a stale source version")
                result = results.get(task_id, {"status": "missing"})
                if result["status"] != "success":
                    failures[task_id] = result
                    continue
                asset = result["asset"]
                if request.get("impact", {}).get("reuse_timeline_positions"):
                    old = request.get("assets", {}).get(task["block_id"])
                    if old and old["task_id"] == task_id:
                        asset = old
                if asset["block_id"] != task["block_id"] or asset["task_id"] != task_id:
                    raise ValueError("Branch asset identity mismatch")
                verify_file(self.root, asset["raw"])
                verify_file(self.root, asset["processed"])
                assets[task["block_id"]] = asset
            return {"intermediate": {"assets": assets, "failures": failures}}
        def align(s):
            request, data = s["request"], s["intermediate"]
            if request.get("impact", {}).get("reuse_timeline_positions") and request.get("timeline"):
                timeline = dict(request["timeline"], script_sha256=digest(request["script"]))
                timeline["timeline_version"] = "tl_" + digest({k: v for k, v in timeline.items() if k != "timeline_version"})[:24]
            else:
                timeline = schedule(request["script"], data["assets"], request["config"], request["visual"])
                feedback = obtain(request["script"], request["performance"], timeline, request["config"], Path(request["input_root"]), self.visual_adapter, self.root)
                if feedback["status"] in {"measured_feedback", "external_locked_feedback"}:
                    timeline = schedule(request["script"], data["assets"], request["config"], feedback)
                    if self.visual_adapter:
                        for _ in range(2):
                            feedback = obtain(request["script"], request["performance"], timeline, request["config"], Path(request["input_root"]), self.visual_adapter, self.root)
                            updated = schedule(request["script"], data["assets"], request["config"], feedback)
                            if updated["timeline_version"] == timeline["timeline_version"]:
                                timeline = updated
                                break
                            timeline = updated
                    if feedback.get("engine_final_timeline_version") != timeline["timeline_version"]:
                        timeline["visual"]["engine_validation"] = "unverified"
                        timeline["issues"].append({"code": "visual_final_timeline_unverified", "severity": "warning", "message": "Measured geometry is available; engine execution has not validated the resulting final timeline"})
                for _ in range(3):
                    placements = {p["block_id"]: p for p in timeline["placements"]}
                    changed = False
                    for e in request["script"]["events"]:
                        end_id = e["media"].get("loop_until")
                        if not end_id:
                            continue
                        if e["kind"] not in {"ambience", "music"} or end_id == e["block_id"] or end_id not in placements:
                            raise ValueError("Background loop target invalid")
                        frames = placements[end_id]["end_frame"] - placements[e["block_id"]]["start_frame"]
                        asset = data["assets"][e["block_id"]]
                        if frames != asset["processed"]["frames"]:
                            data["assets"][e["block_id"]] = loop_asset(self.root, asset, frames)
                            changed = True
                    if not changed:
                        break
                    timeline = schedule(request["script"], data["assets"], request["config"], timeline["visual"])
                else:
                    timeline["issues"].append({"code": "background_extension_unstable", "severity": "blocking"})
                if timeline["visual"].get("engine_final_timeline_version") and timeline["visual"]["engine_final_timeline_version"] != timeline["timeline_version"]:
                    timeline["visual"]["engine_validation"] = "unverified"
            return {"intermediate": data | {"timeline": timeline}}
        def draft(s):
            request, data = s["request"], s["intermediate"]
            product = preview(self.root, request["revision"], request["script"], request["performance"], request["tasks"], data["assets"], data["timeline"], request["config"], data["failures"], request.get("source_record"), self.call_budget.stats(self.batch_id))
            return {"output": data | {"preview": product, "status": "awaiting_review"}}
        integration = self.linear([("version_asset_barrier", collect), ("measured_timeline_visual_feedback", align), ("mix_preview_technical_checks", draft)])
        self.subgraphs = {"content": content, "performance": performance, "voice": voice, "sfx": sounds, "visual": visual, "integration": integration}

        parent = StateGraph(ProductionState)
        def call_content(s):
            try:
                return content.invoke({"request": s})["output"] | {"status": "standardized"}
            except Exception as exc:
                return {"problems": [{"code": "input_contract_invalid", "error_type": type(exc).__name__, "severity": "blocking"}], "status": "needs_input"}
        def wait_input(s):
            answer = interrupt({"kind": "input_required", "problems": s.get("problems", []), "required": "Provide a complete corrected input_script; source instructions are treated as data"})
            safe_config(answer)
            if answer.get("cancel"):
                return {"status": "cancelled"}
            return {"input_script": answer["input_script"], "config": answer.get("config", s["config"]), "problems": [], "status": "retry_input"}
        def call_performance(s):
            try:
                return performance.invoke({"request": s})["output"] | {"problems": []}
            except Exception as exc:
                return {"problems": [{"code": "performance_or_adapter_configuration", "error_type": type(exc).__name__, "task_id": getattr(exc, "task_id", None), "diagnostics": getattr(exc, "diagnostics", None), "severity": "blocking"}], "status": "needs_input"}
        parent.add_node("content", call_content)
        parent.add_node("input_review", wait_input)
        parent.add_node("performance", call_performance)
        parent.add_node("voice", lambda s: {"voice_results": voice.invoke({"tasks": s["tasks"], "config": s["config"], "results": {}}).get("results", {})})
        parent.add_node("sfx", lambda s: {"sfx_results": sounds.invoke({"tasks": s["tasks"], "config": s["config"], "results": {}}).get("results", {})})
        parent.add_node("visual", lambda s: visual.invoke({"request": s})["output"])
        parent.add_node("integration", lambda s: integration.invoke({"request": s})["output"])
        def wait_review(s):
            decision = interrupt({"kind": "publish_review", "status": s["status"], "preview": s["preview"]["preview"], "bundle": s["preview"]["bundle"], "technical_passed": s["preview"]["checks"]["technical_passed"], "choices": ["approve", "changes", "cancel", "retry_after_recovery"]})
            safe_config(decision)
            return {"decision": decision}
        def apply_review(s):
            decision, bundle = s["decision"], s["preview"]["bundle"]
            if decision.get("bundle_sha256") != bundle["bundle_sha256"]:
                raise ValueError("Review decision is not bound to current artifacts")
            action = decision.get("decision")
            if action not in {"approve", "changes", "cancel", "retry_after_recovery"}:
                raise ValueError("Unknown review action")
            notes = list(decision.get("notes", []))
            if action == "changes":
                notes.append({"requested_changes": decision.get("changes", {})})
            if action == "retry_after_recovery":
                notes.append({"request_action": "retry_after_recovery"})
            record = ReviewRecord(review_id="review_" + digest(decision)[:24], bundle_sha256=bundle["bundle_sha256"], source_version_id=s["script"]["source_version_id"], timeline_version=s["timeline"]["timeline_version"], decision="changes" if action == "retry_after_recovery" else action, reviewer=decision.get("reviewer", "unspecified"), scope=decision.get("scope", []), notes=notes, confirmed_points=decision.get("confirmed_points", []), delivery={"status": "pending_validation" if action == "approve" else "not_published"}).model_dump()
            save(self.root / "reviews" / (record["review_id"] + ".json"), record)
            if action == "changes":
                script, cfg, impact = revise(s["script"], s["config"], decision.get("changes", {}), s["revision"] + 1)
                impact["reuse_performance"] = s["performance"]["template_version"] == TEMPLATE_VERSION and design_input_fingerprint(s["script"], s["config"]) == design_input_fingerprint(script, cfg)
                safe_config(cfg)
                save(self.root / "revisions" / f"r{s['revision'] + 1}.json", impact)
                return {"input_script": script, "config": cfg, "revision": s["revision"] + 1, "impact": impact, "status": "revise"}
            if action == "retry_after_recovery":
                # Recovery is an explicit ledger action performed before this resume command.
                return {"revision": s["revision"] + 1, "impact": {}, "status": "revise"}
            if action not in {"approve", "cancel"}:
                raise ValueError("Unknown review action")
            return {"status": "approved_pending_hash_check" if action == "approve" else "cancelled"}
        parent.add_node("review_wait", wait_review)
        parent.add_node("apply_review", apply_review)
        parent.add_node("publish", lambda s: {"delivery": publish(self.root, s), "status": "delivered"})
        parent.add_edge(START, "content")
        parent.add_conditional_edges("content", lambda s: "input_review" if s.get("problems") else "performance")
        parent.add_conditional_edges("input_review", lambda s: END if s["status"] == "cancelled" else "content")
        parent.add_conditional_edges("performance", lambda s: "input_review" if s.get("problems") else ["voice", "sfx", "visual"])
        parent.add_edge(["voice", "sfx", "visual"], "integration")
        parent.add_edge("integration", "review_wait")
        parent.add_edge("review_wait", "apply_review")
        parent.add_conditional_edges("apply_review", lambda s: "content" if s["status"] == "revise" else "publish" if s["status"] == "approved_pending_hash_check" else END)
        parent.add_edge("publish", END)
        return parent.compile(checkpointer=self.checkpointer)

    def invocation(self, thread_id: str):
        return {"configurable": {"thread_id": safe_id(thread_id)}, "recursion_limit": 100}

    @contextmanager
    def execution(self, thread_id: str):
        # One production per output root. SQLite releases this process lock on crash.
        # It prevents two CLI processes from advancing the same checkpoint concurrently.
        db = sqlite3.connect(self.root / "execution.sqlite", timeout=1)
        try:
            db.execute("CREATE TABLE IF NOT EXISTS owner (singleton INTEGER PRIMARY KEY CHECK(singleton=1), thread_id TEXT NOT NULL)")
            db.commit()
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT thread_id FROM owner WHERE singleton=1").fetchone()
            if row and row[0] != thread_id:
                raise ValueError("Output root already belongs to another production thread")
            if not row:
                db.execute("INSERT INTO owner VALUES(1,?)", (thread_id,))
            # Commit ownership, then retain an exclusive transaction while advancing the graph.
            db.commit()
            db.execute("BEGIN IMMEDIATE")
            yield
            db.commit()
        finally:
            db.close()

    def run(self, thread_id: str, script: dict, config: dict, input_root: Path, source_record=None):
        safe_config(script)
        safe_config(config)
        safe_config(source_record or {})
        with self.execution(thread_id):
            if self.graph.get_state(self.invocation(thread_id)).values:
                raise ValueError("Thread already exists; resume it or choose a new root")
            return self.graph.invoke(dict(input_script=script, config=config, input_root=str(input_root.resolve()), source_record=source_record or {}, revision=1, impact={}), self.invocation(thread_id))

    def resume(self, thread_id: str, answer: dict):
        safe_config(answer)
        with self.execution(thread_id):
            return self._resume(thread_id, answer)

    def _resume(self, thread_id: str, answer: dict):
        snapshot = self.graph.get_state(self.invocation(thread_id))
        if not snapshot.values or not snapshot.next:
            raise ValueError("Thread has no pending node")
        if set(snapshot.next) <= {"apply_review", "publish"}:
            # A validation exception cannot strand the operator with an uneditable old decision.
            # Resubmit the corrected decision at the side-effect-free review boundary.
            self.graph.update_state(self.invocation(thread_id), {"decision": answer}, as_node="review_wait")
            return self.graph.invoke(None, self.invocation(thread_id))
        return self.graph.invoke(Command(resume=answer), self.invocation(thread_id))

    def continue_failed(self, thread_id: str):
        with self.execution(thread_id):
            return self.graph.invoke(None, self.invocation(thread_id))

    def status(self, thread_id: str):
        snapshot = self.graph.get_state(self.invocation(thread_id))
        return {"status": snapshot.values.get("status", "not_started"), "next": list(snapshot.next), "revision": snapshot.values.get("revision"), "preview": snapshot.values.get("preview"), "delivery": snapshot.values.get("delivery"), "problems": snapshot.values.get("problems"), "call_budget": self.call_budget.stats("r" + str(snapshot.values.get("revision", 1)))}
