"""Framework-agnostic application service for story planning and delivery."""
from __future__ import annotations

import uuid
from typing import Any

from pydantic import TypeAdapter

from ..llm.usage_context import usage_scope
from .adapters import PlanGenerator, StoryExecutor
from .performance import validate_story_delivery
from .delivery import validate_delivery_integrity
from .operations import revision_impact, operation_errors, source_delivery, operation_requirements
from .repository import WorkflowConflictError, WorkflowNotFoundError
from .repository import _rule_versions
from .schemas import (
    AudienceBand,
    CharacterSnapshot,
    CreateStoryRequest,
    CreationPreferences,
    GenerationJob,
    OperationContext,
    PlanPreview,
    PlanStatus,
    ResolvedSourceReference,
    StoryIntent,
    StoryModeChoice,
    StoryOperationRequest,
    StoryVersionStatus,
    WorkflowJobStatus,
    plan_fingerprint,
    utcnow,
)
from .strategy import StoryStrategyResolver, ResolvedStoryStrategy
from ..config import RuleLibrary

_REQUEST_ADAPTER = TypeAdapter(StoryOperationRequest)
_CANON_LOCKED_FIELDS = {
    "character_id", "name", "appearance", "gender", "species",
    "character_type", "hardware_identity", "device_type", "immutable_facts",
}


class GenerationCancelled(WorkflowConflictError):
    """Cancellation requested before a generated candidate was published."""


class StoryWorkflowService:
    def __init__(
        self, repository, plan_generator: PlanGenerator, story_executor: StoryExecutor,
        template_resolver=None,
    ):
        self.repository = repository
        self.plan_generator = plan_generator
        self.story_executor = story_executor
        self.template_resolver = template_resolver
        self._lib = RuleLibrary.load()
        self._strategy_resolver = StoryStrategyResolver(self._lib)

    def prepare_story_plan(
        self, request: StoryOperationRequest | dict[str, Any],
        operation_context: OperationContext | None = None,
    ) -> PlanPreview:
        request = request if hasattr(request, "intent") else _REQUEST_ADAPTER.validate_python(request)
        operation_requirements(request.model_dump(mode="json"))
        if request.intent == StoryIntent.CONTINUE and request.continuation_kind == "resume_playback":
            raise WorkflowConflictError("PLAYBACK_RESUME_NOT_A_GENERATION_OPERATION")
        context = operation_context or OperationContext()
        inherited_fields = []
        source = None
        if request.intent != StoryIntent.CREATE:
            source = self.repository.resolve_source(request.source_selector, context.principal_id)
            if request.intent == StoryIntent.REVISE:
                target_episode_id = request.edit.target.get("episode_id") or source.episode_id
                if target_episode_id:
                    delivery = self.repository.get_story_version(source.story_version_id)
                    selected = next((ep for ep in delivery.episodes if ep.episode_id == target_episode_id), None)
                    if selected:
                        content_snapshot = dict(source.source_content_snapshot)
                        content_snapshot["selected_episode"] = selected.model_dump(mode="json")
                        source = source.model_copy(update={"source_content_snapshot": content_snapshot})
        characters = self._resolve_characters(request, context)
        original_explicit = sorted(request.creation_preferences.model_fields_set)
        if source and request.intent in (StoryIntent.CONTINUE, StoryIntent.REVISE):
            original = source_delivery(source)
            previous_plan = self.repository.get_plan(original.approved_plan_id)
            inherited = previous_plan.resolved_preferences.model_dump() if previous_plan else {
                "story_mode": original.story_mode, "target_duration_sec": original.episodes[-1].duration_target_sec,
            }
            inherited.pop("target_episodes", None)
            overrides = request.creation_preferences.model_dump(exclude_unset=True)
            inherited_fields = sorted(set(inherited) - set(overrides))
            preferences = CreationPreferences.model_validate({**inherited, **overrides})
            request = request.model_copy(update={"creation_preferences": preferences})
            if not request.characters.selected_character_ids:
                characters = original.character_snapshot
                inherited_fields.append("characters")
            else:
                old_by_id = {c.character_id: c for c in original.character_snapshot}
                if {c.character_id for c in characters} != set(old_by_id) or any(
                    c.canon != old_by_id[c.character_id].canon or c.role_id != old_by_id[c.character_id].role_id
                    or c.performer_id != old_by_id[c.character_id].performer_id for c in characters
                ):
                    raise WorkflowConflictError("CHARACTER_IDENTITY_CONFLICT")
        impact_analysis = self._analyze_impact(request, source)
        if request.intent == StoryIntent.REVISE:
            request = request.model_copy(update={"creation_preferences": request.creation_preferences.model_copy(
                update={"target_episodes": len(impact_analysis["affected_episode_indexes"])}
            )})
        explicit_fields = original_explicit
        preferences, auto_fields = self._resolve_preferences(request)

        # Resolve story strategy (mode + genre + recipe + template)
        # Template → Recipe: if template declares a default_recipe and user hasn't
        # explicitly specified one, use it. Priority:
        #   1. user explicit recipe_id
        #   2. template defaults.default_recipe
        #   3. genre default recipe
        #   4. generic fallback
        template_default_recipe = None
        if request.template_ref and self.template_resolver:
            try:
                spec = self.template_resolver._repo.get(
                    request.template_ref.template_id,
                    version=request.template_ref.template_revision,
                )
                if spec and spec.defaults:
                    template_default_recipe = spec.defaults.get("default_recipe")
            except LookupError:
                pass  # Template not found: optional default missing, not an error
            except AttributeError:
                pass  # repository may not support version parameter; use latest

        effective_recipe = preferences.recipe_id or template_default_recipe
        strategy = self._strategy_resolver.resolve(
            story_mode=preferences.story_mode if preferences.story_mode != StoryModeChoice.AUTO else "auto",
            genre=preferences.genre,
            recipe_id=effective_recipe,
            template_ref=request.template_ref.template_id if request.template_ref else None,
            user_instruction=request.user_instruction,
            explicit_fields=explicit_fields,
        )
        strategy_snapshot = strategy.model_dump(mode="json")

        # Update preferences with resolved values
        if preferences.story_mode == StoryModeChoice.AUTO:
            preferences = preferences.model_copy(update={"story_mode": strategy.story_mode})
        if preferences.genre == "auto":
            preferences = preferences.model_copy(update={"genre": strategy.genre_id})

        template_snapshot = None
        if request.template_ref:
            if self.template_resolver is None:
                raise WorkflowConflictError("TEMPLATE_RESOLVER_NOT_CONFIGURED")
            resolved = self.template_resolver.resolve(
                template_id=request.template_ref.template_id,
                version=request.template_ref.template_revision,
                mode_key=preferences.story_mode,
            )
            template_snapshot = resolved.model_dump(mode="json")
            # Update strategy with actual template version
            strategy.template_version = resolved.version
            strategy.template_ref = resolved.template_id
            strategy_snapshot = strategy.model_dump(mode="json")
        rule_versions = _rule_versions()
        request = request.model_copy(update={"creation_preferences": preferences})
        plan_id = uuid.uuid4().hex
        trace_start = len(getattr(getattr(self.plan_generator, "provider", None), "call_history", []))
        with usage_scope(
            principal_id=context.principal_id,
            request_id=request.request_id,
            plan_id=plan_id,
            stage="planning",
            node="outline",
            attempt_kind="initial_plan",
        ):
            content = self.plan_generator.generate(
                request, source, characters, template=template_snapshot,
                strategy=strategy,
            )
        content = self._normalize_operation_plan(request, source, impact_analysis, content)
        model_trace = list(getattr(getattr(self.plan_generator, "provider", None), "call_history", [])[trace_start:])
        self._validate_plan_shape(content, preferences)
        payload = {
            "plan": content.model_dump(mode="json"),
            "preferences": preferences.model_dump(mode="json"),
            "characters": [item.model_dump(mode="json") for item in characters],
            "source": source.model_dump(mode="json") if source else None,
            "template": template_snapshot,
            "strategy": strategy_snapshot,
            "impact_analysis": impact_analysis,
            "rule_versions": rule_versions,
        }
        preview = PlanPreview(
            plan_id=plan_id,
            plan_revision=1,
            status=PlanStatus.AWAITING_APPROVAL,
            intent=request.intent,
            source_reference=source,
            template_snapshot=template_snapshot,
            strategy_snapshot=strategy_snapshot,
            plan=content,
            resolved_preferences=preferences,
            character_snapshots=characters,
            character_profile_revision_map={item.character_id: item.profile_revision for item in characters},
            auto_filled_fields=auto_fields,
            inherited_fields=inherited_fields,
            impact_analysis=impact_analysis,
            model_trace=model_trace,
            rule_versions=rule_versions,
            explicit_fields=explicit_fields,
            plan_fingerprint=plan_fingerprint(payload),
        )
        result = self.repository.create_plan(request, context, preview)
        self.repository.commit()
        return result

    def revise_story_plan(self, plan_id: str, expected_revision: int, feedback: str) -> PlanPreview:
        if not feedback.strip():
            raise ValueError("feedback is required")
        current = self.repository.get_plan(plan_id)
        raw_request = self.repository.get_plan_request(plan_id)
        if current is None or raw_request is None:
            raise WorkflowNotFoundError(plan_id)
        if current.status != PlanStatus.AWAITING_APPROVAL or current.plan_revision != expected_revision:
            raise WorkflowConflictError("STALE_PLAN")
        if current.rule_versions != _rule_versions():
            raise WorkflowConflictError("STALE_PLAN")
        request = _REQUEST_ADAPTER.validate_python(raw_request)
        trace_start = len(getattr(getattr(self.plan_generator, "provider", None), "call_history", []))
        # Reconstruct strategy from frozen snapshot so revision doesn't lose it
        revision_strategy = None
        if current.strategy_snapshot:
            revision_strategy = ResolvedStoryStrategy.model_validate(current.strategy_snapshot)
        with usage_scope(
            request_id=request.request_id,
            plan_id=plan_id,
            stage="planning",
            node="outline",
            attempt_kind="user_plan_revision",
        ):
            content = self.plan_generator.generate(
                request, current.source_reference, current.character_snapshots,
                previous=current.plan, feedback=feedback, template=current.template_snapshot,
                strategy=revision_strategy,
            )
        content = self._normalize_operation_plan(request, current.source_reference, current.impact_analysis, content)
        model_trace = list(getattr(getattr(self.plan_generator, "provider", None), "call_history", [])[trace_start:])
        if not content.change_summary:
            content = content.model_copy(update={"change_summary": [feedback.strip()]})
        self._validate_plan_shape(content, current.resolved_preferences)
        payload = {
            "plan": content.model_dump(mode="json"),
            "preferences": current.resolved_preferences.model_dump(mode="json"),
            "characters": [item.model_dump(mode="json") for item in current.character_snapshots],
            "source": current.source_reference.model_dump(mode="json") if current.source_reference else None,
            "template": current.template_snapshot,
            "strategy": current.strategy_snapshot,
            "impact_analysis": current.impact_analysis,
            "rule_versions": current.rule_versions,
        }
        revised = current.model_copy(update={
            "plan_revision": expected_revision + 1,
            "plan": content,
            "plan_fingerprint": plan_fingerprint(payload),
            "model_trace": [*current.model_trace, *model_trace],
            "updated_at": utcnow(),
        })
        result = self.repository.save_revision(revised, feedback)
        self.repository.commit()
        return result

    def approve_story_plan(
        self, plan_id: str, expected_revision: int,
        fingerprint: str, idempotency_key: str,
    ) -> GenerationJob:
        plan = self.repository.get_plan(plan_id)
        if plan is None:
            raise WorkflowNotFoundError(plan_id)
        result = self.repository.approve_and_create_job(
            plan_id, expected_revision, fingerprint, idempotency_key
        )
        self.repository.commit()
        return result

    def generate_from_approved_plan(self, job_id: str):
        job = self.repository.get_job(job_id)
        if job is None:
            raise WorkflowNotFoundError(job_id)
        if job.status == WorkflowJobStatus.SUCCEEDED and job.result_story_version_id:
            return self.repository.get_story_version(job.result_story_version_id)
        if job.status != WorkflowJobStatus.QUEUED:
            raise WorkflowConflictError(f"JOB_NOT_RUNNABLE: {job.status}")
        try:
            running = self.repository.claim_job(job_id)
            self.repository.commit()
        except Exception:
            self.repository.rollback()
            raise
        try:
            with usage_scope(
                request_id=job.request_id,
                job_id=job.job_id,
                plan_id=job.approved_plan_id,
                stage="generation",
                attempt_kind="approved_job",
            ):
                delivery = self.story_executor.execute(job.input_snapshot)
            latest = self.repository.lock_job(job_id)
            if latest.execution_token != running.execution_token:
                raise WorkflowConflictError("JOB_EXECUTION_FENCED")
            if latest.status == WorkflowJobStatus.CANCEL_REQUESTED:
                self.repository.save_job(latest.model_copy(update={
                    "status": WorkflowJobStatus.CANCELLED, "stage": "cancelled",
                }))
                self.repository.commit()
                raise GenerationCancelled("JOB_CANCELLED")
            validating = running.model_copy(update={
                "status": WorkflowJobStatus.VALIDATING, "stage": "validating", "progress_pct": 90,
            })
            self.repository.save_job(validating)
            self.repository.commit()
            report = validate_story_delivery(delivery)
            executor_report = delivery.quality_report
            report = report.model_copy(update={
                "outline_alignment_passed": executor_report.outline_alignment_passed,
                "continuity_passed": executor_report.continuity_passed,
                "content_rating_passed": executor_report.content_rating_passed,
                "warnings": [*report.warnings, *executor_report.warnings],
                "operation_checks": executor_report.operation_checks,
                "timeline_claims": executor_report.timeline_claims,
            })
            approved = job.input_snapshot.preview
            integrity = validate_delivery_integrity(delivery)
            op_errors = operation_errors(job.input_snapshot, delivery)
            generated_ids = delivery.generated_episode_ids
            generated_episodes = [ep for ep in delivery.episodes if ep.episode_id in generated_ids]
            episode_ready = (
                len(generated_episodes) == approved.resolved_preferences.target_episodes
                and all(ep.ready_for_playback and ep.validation_status == "PASSED" for ep in delivery.episodes)
                and [ep.index for ep in delivery.episodes] == list(range(1, len(delivery.episodes) + 1))
                and all(ep.duration_target_sec == approved.resolved_preferences.target_duration_sec for ep in generated_episodes)
            )
            snapshot_match = (
                delivery.approved_plan_id == approved.plan_id
                and delivery.approved_plan_revision == approved.plan_revision
                and delivery.outline == approved.plan
                and delivery.story_mode == approved.resolved_preferences.story_mode
                and delivery.content_form == approved.resolved_preferences.content_form
            )
            listen_first = getattr(getattr(self.story_executor, "provider", None),
                                   "production_review_policy", None) == "listen_first"
            semantic_ready = (report.outline_alignment_passed and report.continuity_passed
                              and report.content_rating_passed)
            ready = (
                report.validation_status == "PASSED"
                and executor_report.validation_status == "PASSED"
                and (listen_first or semantic_ready)
                and episode_ready and snapshot_match and integrity.valid and not op_errors
            )
            delivery = delivery.model_copy(update={
                "quality_report": report,
                "ready_for_playback": ready,
                "status": StoryVersionStatus.READY if ready else StoryVersionStatus.CANDIDATE,
            })
            if not ready:
                failed_checks = [
                    name for name, passed in {
                        "delivery_validation": report.validation_status == "PASSED",
                        "executor_validation": executor_report.validation_status == "PASSED",
                        "outline_alignment": report.outline_alignment_passed,
                        "continuity": report.continuity_passed,
                        "content_rating": report.content_rating_passed,
                        "episode_ready": episode_ready,
                        "approved_snapshot": snapshot_match,
                    }.items() if not passed
                ]
                raise ValueError(
                    "PERFORMANCE_OR_STORY_VALIDATION_FAILED: " + ", ".join(failed_checks)
                    + "; " + "; ".join([*report.warnings, *integrity.errors, *op_errors][:8])
                )
            latest = self.repository.lock_job(job_id)
            if latest.execution_token != running.execution_token:
                raise WorkflowConflictError("JOB_EXECUTION_FENCED")
            if latest.status == WorkflowJobStatus.CANCEL_REQUESTED:
                self.repository.save_job(latest.model_copy(update={"status": WorkflowJobStatus.CANCELLED, "stage": "cancelled"}))
                self.repository.commit()
                raise GenerationCancelled("JOB_CANCELLED")
            delivery = self.repository.save_story_version(delivery)
            succeeded = validating.model_copy(update={
                "status": WorkflowJobStatus.SUCCEEDED,
                "stage": "completed",
                "progress_pct": 100,
                "result_story_version_id": delivery.story_version_id,
            })
            self.repository.save_job(succeeded)
            self.repository.commit()
            return delivery
        except GenerationCancelled:
            raise
        except Exception as exc:
            self.repository.rollback()
            current = self.repository.lock_job(job_id)
            if current.execution_token != running.execution_token:
                self.repository.rollback()
                raise
            failed = running.model_copy(update={
                "status": WorkflowJobStatus.FAILED,
                "stage": "failed",
                "error_code": getattr(getattr(exc, "code", None), "value", None) or "WORKFLOW_GENERATION_FAILED",
                "error_message": str(exc),
            })
            self.repository.save_job(failed)
            self.repository.commit()
            raise

    def get_story_plan(self, plan_id: str):
        return self.repository.get_plan(plan_id)

    def get_generation_job(self, job_id: str):
        return self.repository.get_job(job_id)

    def get_story_version(self, story_version_id: str):
        return self.repository.get_story_version(story_version_id)

    def resolve_story_reference(self, selector, operation_context: OperationContext | None = None):
        context = operation_context or OperationContext()
        return self.repository.resolve_source(selector, context.principal_id)

    def cancel_generation_job(self, job_id: str):
        job = self.repository.lock_job(job_id)
        if job is None:
            raise WorkflowNotFoundError(job_id)
        if job.status == WorkflowJobStatus.QUEUED:
            updated = job.model_copy(update={"status": WorkflowJobStatus.CANCELLED, "stage": "cancelled"})
        elif job.status in (WorkflowJobStatus.RUNNING, WorkflowJobStatus.VALIDATING):
            updated = job.model_copy(update={"status": WorkflowJobStatus.CANCEL_REQUESTED})
        else:
            return job
        result = self.repository.save_job(updated)
        self.repository.commit()
        return result

    def recover_interrupted_job(self, job_id: str, expected_execution_token: str):
        """Explicitly fence an interrupted worker; stale workers cannot publish."""
        result = self.repository.fence_job(job_id, expected_execution_token)
        self.repository.commit()
        return result

    def retry_failed_job(self, job_id: str):
        job = self.repository.lock_job(job_id)
        if job is None or job.status != WorkflowJobStatus.FAILED or job.result_story_version_id:
            raise WorkflowConflictError("JOB_NOT_RETRYABLE")
        result = self.repository.save_job(job.model_copy(update={"status": WorkflowJobStatus.QUEUED,
            "stage": "queued", "progress_pct": 0, "error_code": None, "error_message": None}))
        self.repository.commit()
        return result

    def update_character_profile(
        self, character_id: str, expected_revision: int, patch: dict[str, Any],
        operation_context: OperationContext | None = None,
    ):
        forbidden = sorted(_CANON_LOCKED_FIELDS & set(patch))
        if forbidden:
            raise ValueError(f"CHARACTER_CANON_LOCKED: {forbidden}")
        context = operation_context or OperationContext()
        result = self.repository.update_character_profile(
            context.principal_id, character_id, expected_revision, patch
        )
        self.repository.commit()
        return result

    def record_playback_event(self, event: dict[str, Any], operation_context=None) -> None:
        context = operation_context or OperationContext()
        payload = dict(event)
        payload.setdefault("event_id", uuid.uuid4().hex)
        payload["principal_id"] = context.principal_id
        self.repository.record_playback_event(payload)
        self.repository.commit()

    @staticmethod
    def _resolve_preferences(request):
        prefs = request.creation_preferences
        auto_fields = sorted(set(type(prefs).model_fields) - set(prefs.model_fields_set))
        if prefs.story_mode == StoryModeChoice.AUTO:
            # Defer to strategy resolver (called in prepare_story_plan)
            auto_fields.append("story_mode")
        if not prefs.recipe_id:
            auto_fields.append("recipe_id")
        if prefs.genre == "auto":
            auto_fields.append("genre")
        return prefs, auto_fields

    @staticmethod
    def _resolve_characters(request, context: OperationContext) -> list[CharacterSnapshot]:
        output: list[CharacterSnapshot] = []
        bindings = {
            item.get("character_id"): item
            for item in request.characters.role_bindings
            if item.get("character_id")
        }
        for character_id in request.characters.selected_character_ids:
            raw = context.character_snapshots.get(character_id)
            if raw is None:
                raise ValueError(f"CHARACTER_NOT_FOUND: {character_id}")
            canon = dict(raw.get("canon") or {})
            profile = dict(raw.get("profile") or {})
            overrides = request.characters.temporary_profile_overrides.get(character_id, {})
            forbidden = sorted(_CANON_LOCKED_FIELDS & set(overrides))
            if forbidden:
                raise ValueError(f"CHARACTER_CANON_LOCKED: {forbidden}")
            profile.update(overrides)
            current_revision = raw.get("profile_revision", 1)
            requested_revision = request.characters.character_profile_revisions.get(character_id)
            if requested_revision is not None and requested_revision != current_revision:
                raise WorkflowConflictError("CHARACTER_VERSION_CONFLICT")
            binding = bindings.get(character_id, {})
            output.append(CharacterSnapshot(
                character_id=character_id,
                canon_revision=raw.get("canon_revision", 1),
                profile_revision=current_revision,
                canon=canon,
                profile=profile,
                role_id=binding.get("story_role_id"),
                performer_id=binding.get("performer_id"),
            ))
        return output

    @staticmethod
    def _validate_plan_shape(content, preferences) -> None:
        if len(content.episode_outlines) != preferences.target_episodes:
            raise ValueError("plan episode count does not match target_episodes")
        if any(item.target_duration_sec != preferences.target_duration_sec for item in content.episode_outlines):
            raise ValueError("plan duration does not match target_duration_sec")

    def _analyze_impact(self, request, source):
        if request.intent != StoryIntent.REVISE or source is None:
            return {}
        delivery = self.repository.get_story_version(source.story_version_id)
        if delivery is None:
            raise WorkflowNotFoundError(source.story_version_id)
        try:
            return revision_impact(request.edit, delivery, source.episode_id)
        except ValueError as exc:
            raise WorkflowConflictError(str(exc)) from exc

    @staticmethod
    def _normalize_operation_plan(request, source, impact, content):
        if source and request.intent in (StoryIntent.CONTINUE, StoryIntent.REVISE):
            original = source_delivery(source)
            count = len(content.episode_outlines)
            indexes = (list(range(len(original.episodes) + 1, len(original.episodes) + 1 + count))
                       if request.intent == StoryIntent.CONTINUE else impact["affected_episode_indexes"])
            if len(indexes) != count:
                raise ValueError("plan episode count differs from operation scope")
            # Story names and voices are source identities, not a chance to recast.
            previous_portrayals = original.outline.character_portrayals
            previous_voices = original.outline.fictional_voice_roles
            content = content.model_copy(update={
                "episode_outlines": [ep.model_copy(update={"index": i}) for ep, i in zip(content.episode_outlines, indexes)],
                "character_portrayals": previous_portrayals,
                "fictional_voice_roles": previous_voices,
            })
        return content
