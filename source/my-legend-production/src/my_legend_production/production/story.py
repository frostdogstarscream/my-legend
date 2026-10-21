"""StoryGenerate adapter: durable submission, explicit policy decisions, immutable handoff."""
from __future__ import annotations

import re
import httpx

from ..core import AdapterError, source_check
from .contracts import StoryOperationSource, StageResult
from .repository import ProductionError


class StoryClient:
    def __init__(self, url, token):
        self.client = httpx.Client(base_url=url, headers={"Authorization": "Bearer " + token},
                                  timeout=20, trust_env=False, follow_redirects=False)

    def call(self, method, path, body=None):
        try:
            response = self.client.request(method, path, json=body) if body is not None else self.client.request(method, path)
        except httpx.HTTPError:
            raise ProductionError("STORY_SERVICE_UNAVAILABLE") from None
        if response.status_code >= 300:
            raise ProductionError("STORY_REQUEST_REJECTED" if response.status_code < 500 else "STORY_SERVICE_UNAVAILABLE")
        try:
            return response.json()
        except ValueError:
            raise ProductionError("STORY_RESPONSE_INVALID") from None


def validate_handoff(delivery, manifest):
    source_check({"source": {"story_id": delivery["story_id"], "story_version_id": delivery["story_version_id"]},
                  "scope": {"scenes": [{"episode_id": ep["episode_id"], "scene_id": sc["scene_id"]}
                                       for ep in delivery["episodes"] for sc in ep["scenes"]]}}, delivery, manifest)


def plan_body(ctx):
    source = ctx.request.source
    body = {"request_id": "prod-" + ctx.stage["job_id"],
            "client_context": {"production": {"job_id": ctx.stage["job_id"], "request_hash": ctx.stage["request_hash"]}},
            "idempotency_key": "prod-plan-" + ctx.stage["job_id"]}
    if isinstance(source, StoryOperationSource):
        delivery, manifest = ctx.store.json(source.delivery), ctx.store.json(source.manifest)
        validate_handoff(delivery, manifest)
        if delivery["story_version_id"] != source.source_version_id:
            raise ProductionError("SOURCE_VERSION_MISMATCH")
        body.update(intent=source.intent, user_instruction=source.instruction,
                    source_selector={"type": "by_id", "story_version_id": source.source_version_id},
                    creation_preferences={"target_episodes": 1, "target_duration_sec": source.target_duration_seconds})
        if source.intent == "revise":
            body["edit"] = {**source.edit.model_dump(), "instruction": source.instruction}
        elif source.intent == "remix":
            body["remix"] = {**source.remix.model_dump(), "instruction": source.instruction}
            # Remix starts a new story; unlike continue/revise, the engine does
            # not implicitly inherit selected catalog characters or performers.
            snapshots = delivery.get("character_snapshot", [])
            body["characters"] = {
                "selected_character_ids": [c["character_id"] for c in snapshots],
                "character_profile_revisions": {c["character_id"]: c["profile_revision"]
                                                for c in snapshots if c.get("profile_revision", 0) > 0},
                "role_bindings": [{"character_id": c["character_id"], "role_id": c.get("role_id") or c["character_id"],
                                  "performer_id": c.get("performer_id") or "performer-" + c["character_id"]}
                                 for c in snapshots]}
        else:
            body["continuation_kind"] = "next_episode"
    else:
        body.update(intent="create", user_instruction="故事名称：" + source.title + "。创作要求：" + source.idea,
                    creation_preferences={"genre": source.genre, "story_mode": "general", "target_episodes": 1,
                                          "target_duration_sec": source.target_duration_seconds, "ending_preference": "closed"},
                    characters={"selected_character_ids": source.character_ids, "role_bindings": [
                        {"character_id": cid, "role_id": cid, "performer_id": "performer-" + cid} for cid in source.character_ids]})
    return body


class StoryStage:
    """One bounded advance per claim; no client polling is needed.

    The upstream profile must enforce its declared per-task model ceiling. The
    parent reserves that ceiling before submission, then records returned usage.
    """
    def __init__(self, client, *, task_call_ceiling=12):
        self.client = client
        self.task_call_ceiling = task_call_ceiling

    def reserve(self, ctx, phase):
        key = "story:" + phase + "-budget"
        ctx.repository.reserve_call(ctx.stage, key, ctx.stage["request"]["schema_version"] + ":" + phase, self.task_call_ceiling)
        return key

    def account(self, ctx, phase, response):
        usage = response.get("usage", {})
        if not isinstance(usage.get("calls"), int) or usage["calls"] < 0:
            raise ProductionError("STORY_USAGE_MISSING")
        if usage.get("unknown_usage_calls", 0) or usage.get("inflight_usage_unknown", False):
            ctx.repository.observe_call(ctx.stage, "story:" + phase + "-budget", usage["calls"], unknown=True)
            raise ProductionError("EXTERNAL_RESULT_UNKNOWN")
        ctx.repository.complete_call(ctx.stage, "story:" + phase + "-budget", usage, actual_units=usage["calls"])

    def complete(self, ctx, delivery, manifest, decision=None):
        try:
            validate_handoff(delivery, manifest)
        except AdapterError as exc:
            raise ProductionError(exc.issue["code"]) from None
        version = delivery["story_version_id"]
        refs = [ctx.store.put_json("StoryDelivery", version, delivery), ctx.store.put_json("RenderManifest", version, manifest)]
        if decision:
            refs.append(ctx.store.put_json("StoryPlanDecision", version, decision))
        return StageResult(status="succeeded", source_version_id=version, artifacts=refs, technical_quality="passed",
                           human_review="not_required" if ctx.request.mode == "automatic" else "pending")

    def __call__(self, ctx):
        request = ctx.request
        if request.source.kind == "delivery":
            return self.complete(ctx, ctx.store.json(request.source.delivery), ctx.store.json(request.source.manifest))
        state = dict(ctx.stage["checkpoint"])
        source = request.source
        phase = state.get("phase", "submit")
        if phase == "submit":
            self.reserve(ctx, "planning")
            body = plan_body(ctx)
            response = ctx.effect("submit-plan", body, lambda: self.client.call("POST", "/v1/plans", body), replay_safe=True)
            return StageResult(status="running", checkpoint={"phase": "planning", "task_id": response["task_id"]})
        if phase == "planning":
            response = self.client.call("GET", "/v1/tasks/" + state["task_id"])
            if response["status"] in ("FAILED", "CANCELLED"):
                self.account(ctx, "planning", response)
                return self.failed(state, response)
            if response["status"] != "SUCCEEDED":
                return StageResult(status="running", checkpoint=state)
            self.account(ctx, "planning", response)
            plan = self.client.call("GET", "/v1/plans/" + response["result"]["plan_id"])
            state.update(phase="plan_ready", plan=plan)
            if request.mode == "reviewed":
                return StageResult(status="needs_input", checkpoint=state, error_code="OUTLINE_REVIEW_REQUIRED", human_review="pending")
            return StageResult(status="running", checkpoint=state, poll_after_seconds=0)
        if phase == "plan_ready":
            plan = state["plan"]
            expected = {"plan_id": plan["plan_id"], "revision": plan["plan_revision"], "fingerprint": plan["plan_fingerprint"]}
            if request.mode == "reviewed":
                resolution = ctx.stage["resolution"]
                if resolution != {**expected, "decision": "approve"}:
                    return StageResult(status="needs_input", checkpoint=state, error_code="EXACT_OUTLINE_DECISION_REQUIRED", human_review="pending")
            decision = {**expected, "authority": "user" if request.mode == "reviewed" else "configured_automation_policy",
                        "policy_version": "story-plan-proceed/1", "human_approval": request.mode == "reviewed",
                        "request_hash": ctx.stage.get("request_hash"), "job_id": ctx.stage["job_id"]}
            self.reserve(ctx, "writing")
            body = {"expected_revision": expected["revision"], "fingerprint": expected["fingerprint"],
                    "idempotency_key": "prod-write-" + ctx.stage["job_id"]}
            response = ctx.effect("execute-plan", {"request": body, "authority": decision},
                                  lambda: self.client.call("POST", "/v1/plans/" + expected["plan_id"] + "/approve", body), replay_safe=True)
            return StageResult(status="running", checkpoint={"phase": "writing", "upstream_id": response["job_id"], "decision": decision})
        if phase == "writing":
            response = self.client.call("GET", "/v1/jobs/" + state["upstream_id"])
            if response["status"] in ("FAILED", "CANCELLED"):
                self.account(ctx, "writing", response)
                return self.failed(state, response)
            if response["status"] != "SUCCEEDED":
                return StageResult(status="running", checkpoint=state)
            self.account(ctx, "writing", response)
            version = response["result_story_version_id"]
            delivery = self.client.call("GET", "/v1/story-versions/" + version)
            manifest = self.client.call("GET", "/v1/story-versions/" + version + "/render-manifest")
            if delivery.get("story_version_id") != version or manifest.get("story_version_id") != version:
                raise ProductionError("SOURCE_VERSION_MISMATCH")
            if isinstance(source, StoryOperationSource):
                original = ctx.store.json(source.delivery)
                if (version == source.source_version_id or delivery.get("parent_story_version_id") != source.source_version_id
                        or (delivery.get("story_id") == original["story_id"]) != (source.intent != "remix")):
                    raise ProductionError("STORY_OPERATION_VERSION_MISMATCH")
            return self.complete(ctx, delivery, manifest, state["decision"])
        raise ProductionError("INVALID_STORY_CHECKPOINT")

    @staticmethod
    def failed(state, response):
        code = response.get("error_code") or "STORY_GENERATION_FAILED"
        known_policy_errors = {'STORY_REQUIRED_CAPABILITY_UNAVAILABLE', 'STORY_PRODUCTION_READINESS_FAILED',
                               'STORY_AUDIO_REVIEW_INVALID', 'STORY_AUDIO_READINESS_FAILED',
                               'STORY_DRAFT_REPAIR_CONTRACT_INVALID',
                               'STORY_READINESS_REVIEW_INVALID', 'STORY_PLAN_REVIEW_INVALID',
                               'STORY_PLAN_READINESS_FAILED', 'REPAIR_BUDGET_EXCEEDED', 'CALL_BUDGET_EXCEEDED'}
        if response.get('error_message') in known_policy_errors:
            code = response['error_message']
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", code):
            code = "STORY_GENERATION_FAILED"
        return StageResult(status="failed", checkpoint=state, error_code=code)
