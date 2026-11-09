"""Persisted model directing over frozen audio and user-confirmed casting only."""
from copy import deepcopy
from hashlib import sha256
import json
import time

from pydantic import ValidationError
from .contracts import ArtifactRef, Budget, DirectedPerformanceSource, ProductionRequest, StageResult
from .directing_contracts import DirectingPlan, VisualCoverageReview, parse_review, PLANNER, COVERAGE_REVIEWER
from .directing_compile import create_context, compile_plan, required_checks, validate_review, planning_schema
from .performance_delivery import INPUT_KINDS, verify_resources
from .repository import ProductionError
from .spine_preview import local_root, save_preview
from .life_contracts import LifeValidationError, canonical_hash
from .directing_protocol import (planner_input, grouped_schema, resolve_plan, GROUPED_INSTRUCTIONS)
from .directing_review_execution import review_execution
from . import stage_policy

DIRECTING_STAGES={'performance_context','spine_direct','spine_compile','performance_package'}


def prepare_directing_request(repository,store,owner_id,source_job_id,body,frozen_casting=None):
    from .commander_preview import commander_scene_digest
    from .h5_preview import load_h5_catalog
    from .playback import playback_view
    job=repository.get(source_job_id,owner_id)
    if job['request']['source']['kind'] in ('saved_performance','directed_performance'):
        raise ProductionError('H5_ORIGINAL_SOURCE_REQUIRED')
    manifest=playback_view(job,store)['manifest']
    if not manifest or not manifest.get('audio'):raise ProductionError('SPINE_AUDIO_NOT_READY')
    if body.parent_job_id:
        parent=repository.get(body.parent_job_id,owner_id)
        if parent['work_id']!=job['work_id'] or parent['request']['source'].get('source_job_id')!=source_job_id or parent['status']!='succeeded':
            raise ProductionError('H5_PARENT_MISMATCH')
    catalog=load_h5_catalog(local_root(),body.catalog_sha256)
    refs=[ArtifactRef.model_validate(a) for a in job['artifacts'] if a['kind'] in INPUT_KINDS]
    for ref in refs:store.verify(ref)
    source=DirectedPerformanceSource(source_job_id=source_job_id,source_version_id=job['source_version_id'],inputs=refs,
        catalog_sha256=body.catalog_sha256,scene_catalog_sha256=(frozen_casting['scene_catalog_sha256'] if frozen_casting else commander_scene_digest(local_root(),catalog)),
        bindings=body.bindings,initial_outfits=body.initial_outfits,parent_job_id=body.parent_job_id)
    from .asset_capabilities import build_capabilities, save_capabilities
    from .scene_preview import load_scene_catalog
    if frozen_casting:
        source=source.model_copy(update={k:frozen_casting[k] for k in ('scene_catalog_sha256','capability_sha256','visual_policy')})
    else:
        capabilities=build_capabilities(catalog,load_scene_catalog(local_root(),source.scene_catalog_sha256))
        source=source.model_copy(update={'visual_policy':stage_policy.POLICY,
            'capability_sha256':save_capabilities(local_root(),capabilities)})
    try:create_context(job,source,store,local_root())
    except (LifeValidationError,ValidationError):raise ProductionError('DIRECTING_CASTING_INVALID') from None
    return ProductionRequest(idempotency_key=body.idempotency_key,work_id=job['work_id'],source=source,outputs=['h5'],
        budget=Budget(max_provider_calls=6,max_repairs_per_stage=2,max_elapsed_seconds=5400))


def source_job(ctx):
    source=ctx.request.source
    job=ctx.repository.get(source.source_job_id,ctx.stage['owner_id'])
    owned={a['kind']:a for a in job['artifacts']}
    if job['work_id']!=ctx.stage['work_id'] or job['source_version_id']!=source.source_version_id:
        raise ProductionError('H5_SOURCE_BINDING_MISMATCH')
    for ref in source.inputs:
        if owned.get(ref.kind)!=ref.model_dump() or ref.source_version_id!=source.source_version_id:raise ProductionError('H5_SOURCE_BINDING_MISMATCH')
        ctx.store.verify(ref)
    return job


class PerformanceContextStage:
    def __call__(self,ctx):
        job=source_job(ctx);source=ctx.request.source
        value=create_context(job,source,ctx.store,local_root())
        verify_resources(dict(catalog_sha256=source.catalog_sha256,scene_track={'segments':[
            {'scene':{'catalog_sha256':source.scene_catalog_sha256}}]}),check=ctx.check_active)
        ctx.check_active()
        extra=[ctx.store.put_json('StageCapabilityCatalog',source.source_version_id,value['capabilities'])] if stage_policy.asset_first(value) else []
        return StageResult(status='succeeded',source_version_id=source.source_version_id,technical_quality='passed',human_review='pending',
            artifacts=[*source.inputs,*extra,ctx.store.put_json('SpineDirectingContext',source.source_version_id,value)])


class SpineDirectStage:
    def __init__(self,model):self.model=model

    def __call__(self,ctx):
        job=source_job(ctx);version=ctx.request.source.source_version_id
        set_session=getattr(self.model,'set_session',None)
        if callable(set_session):set_session(ctx.stage['job_id'])
        refs={a['kind']:ArtifactRef.model_validate(a) for a in ctx.stage['artifacts']}
        context=ctx.store.json(refs['SpineDirectingContext']);records=[];feedback=None;transport_diagnostic=None
        staged=stage_policy.asset_first(context)
        # Retain executable geometry/timing rules, not the old literal-coverage policy.
        planner=(stage_policy.PLANNER+'\n'+'\n'.join(line for line in GROUPED_INSTRUCTIONS.splitlines()
            if 'gap' not in line and '1.4' not in line)) if staged else PLANNER+'\n'+GROUPED_INSTRUCTIONS
        reviewer=stage_policy.REVIEWER if staged else COVERAGE_REVIEWER
        review_contract=stage_policy.StageReview if staged else VisualCoverageReview
        last_code='DIRECTING_PLAN_INVALID'
        max_repairs=ctx.request.budget.max_repairs_per_stage
        recovered_plan=None;recovery_corrections=[]
        if 'SpineDirectingIssueReport' in refs:
            issue=ctx.store.json(refs['SpineDirectingIssueReport']);previous=issue.get('responses',[])
            # Explicit resume after a local compiler repair: validate the final saved
            # candidate again, then use only its still-unspent review slot.
            if issue.get('error_code')=='DIRECTING_PLAN_INVALID' and previous and previous[-1].get('phase')=='plan' and previous[-1].get('attempt')==max_repairs and previous[-1].get('finish_reason')=='stop':
                records=deepcopy(previous);recovered_plan=previous[-1]['content']
        for attempt in range(max_repairs if recovered_plan is not None else 0,max_repairs+1):
            plan = None; review = None; wire_plan = None; diagnostic_report = None
            def call(name,system,payload,schema):
                nonlocal transport_diagnostic
                remaining=ctx.stage['deadline'].timestamp()-time.time()
                if remaining<=0:raise ProductionError('DEADLINE_EXCEEDED')
                request={'system':system,'payload':payload,'schema':schema}
                def invoke():
                    nonlocal transport_diagnostic
                    try:return self.model.complete(system,payload,schema,remaining)
                    except Exception as exc:
                        transport_diagnostic=getattr(exc,'diagnostic',{'exception_type':type(exc).__name__})
                        raise
                response=ctx.effect(name+':'+str(attempt),request,invoke,units=1)
                records.append({'phase':name,'attempt':attempt,**response})
                if response.get('finish_reason')=='length':raise ProductionError('DIRECTING_OUTPUT_LIMIT')
                return response['content']
            try:
                payload=planner_input(context)
                if feedback is not None:payload['repair_feedback']=feedback
                text=recovered_plan if recovered_plan is not None else call('plan',planner,payload,grouped_schema(context))
                plan=resolve_plan(text,context)
                if recovered_plan is not None and staged:
                    # Preserve the candidate's actual tracks. Correct claims of
                    # new actions on rows that have no new cues, then independently review.
                    assessments=[]
                    for assessment in plan.assessments:
                        if assessment.classification=='performed' and not assessment.cue_ids:
                            recovery_corrections.append(dict(source=assessment.source.model_dump(),
                                old_classification='performed',classification='no_visible_action'))
                            assessment=assessment.model_copy(update=dict(classification='no_visible_action',presentation='audio_led',
                                reason='本行无新增可见指令，由原声音或延续既有状态表达。',simplification='本行不新增可见动作。'))
                        assessments.append(assessment)
                    plan=plan.model_copy(update={'assessments':assessments})
                wire_plan=json.loads(text)
                try:
                    preview,summary=compile_plan(job,ctx.store,context,plan,local_root())
                except LifeValidationError as preflight:
                    # A structurally valid candidate can be reviewed for semantic
                    # problems before the next repair. It is never publishable.
                    if attempt<max_repairs:
                        from .directing_diagnostics import diagnose_plan
                        diagnostic_report=diagnose_plan(wire_plan,context,job,ctx.store,local_root())
                        text=call('diagnose',reviewer,{'input':context['model_input'],'plan':plan.model_dump(),
                            'execution':{'preflight_passed':False,'diagnostics':preflight.diagnostics,
                                         'diagnostic_projection':diagnostic_report},
                            'required_checks':required_checks(context)},review_contract.model_json_schema())
                        try:review=review_contract.model_validate_json(text)
                        except ValidationError:pass
                    raise preflight
                text=call('review',reviewer,{'input':context['model_input'],'plan':plan.model_dump(),
                    'execution':review_execution(preview,summary),'required_checks':required_checks(context)},review_contract.model_json_schema())
                review=review_contract.model_validate_json(text)
                validate_review(review,context,plan)
                ctx.check_active()
                return StageResult(status='succeeded',source_version_id=version,technical_quality='passed',human_review='pending',artifacts=[
                    ctx.store.put_json('SpineDirectingPlan',version,plan.model_dump()),
                    ctx.store.put_json('SpineDirectingReview',version,review.model_dump()),
                    ctx.store.put_json('SpineDirectingEvidence',version,dict(responses=records,execution=summary,repairs=attempt,
                        context_sha256=refs['SpineDirectingContext'].sha256,old_visual_tracks_supplied=False,
                        recovered_saved_plan=recovered_plan is not None,recovery_corrections=recovery_corrections))])
            except (ValidationError,LifeValidationError,ProductionError) as exc:
                if isinstance(exc,ProductionError) and exc.code=='EXTERNAL_RESULT_UNKNOWN':
                    return StageResult(status='needs_input',source_version_id=version,error_code=exc.code,human_review='pending',
                        artifacts=[ctx.store.put_json('SpineDirectingIssueReport',version,dict(error_code=exc.code,
                            transport=transport_diagnostic,message='模型结果未知，停止制作并保留对账状态，不自动重发。',responses=records))])
                if isinstance(exc,ProductionError) and not exc.code.startswith('DIRECTING_'):raise
                last_code=exc.code if isinstance(exc,ProductionError) else 'DIRECTING_PLAN_INVALID'
                details=json.dumps(exc.diagnostics,ensure_ascii=False) if isinstance(exc,LifeValidationError) else str(exc)
                feedback={'error_code':last_code,'details':details[:8000]}
                if getattr(exc,'review_evidence',None):feedback['invalid_review_evidence']=exc.review_evidence
                if plan is not None:feedback['previous_plan']=wire_plan if wire_plan.get('schema_version') in ('spine_directing_plan/1.1','spine_directing_plan/1.2','spine_directing_plan/1.3','spine_directing_plan/1.4','spine_directing_plan/1.5') else plan.model_dump()
                elif records:feedback['previous_response']=records[-1]['content']
                if plan is None and isinstance(exc,ValidationError) and records:
                    from .directing_diagnostics import diagnose_plan
                    diagnostic_report=diagnose_plan(records[-1]['content'],context,job,ctx.store,local_root())
                if diagnostic_report is not None:feedback['preflight_diagnostics']=diagnostic_report
                if review is not None:feedback['previous_review']=review.model_dump()
                elif records and records[-1]['phase'] in ('review','diagnose'):feedback['previous_review_response']=records[-1]['content']
        return StageResult(status='failed',source_version_id=version,error_code=last_code,technical_quality='failed',human_review='pending',
            artifacts=[ctx.store.put_json('SpineDirectingIssueReport',version,dict(error_code=last_code,feedback=feedback,responses=records))])


class SpineCompileStage:
    def __call__(self,ctx):
        from .playback import playback_view
        job=source_job(ctx);source=ctx.request.source
        refs={a['kind']:ArtifactRef.model_validate(a) for a in ctx.stage['artifacts']}
        context=ctx.store.json(refs['SpineDirectingContext']);review=parse_review(ctx.store.json(refs['SpineDirectingReview']))
        plan=ctx.store.json(refs['SpineDirectingPlan']);validate_review(review,context,plan)
        value,summary=compile_plan(job,ctx.store,context,plan,local_root())
        value['directing']=dict(production_job_id=ctx.stage['job_id'],casting_confirmed=True,
            context=refs['SpineDirectingContext'].model_dump(),plan=refs['SpineDirectingPlan'].model_dump(),review=refs['SpineDirectingReview'].model_dump(),
            evidence=refs['SpineDirectingEvidence'].model_dump(),technical_status='passed',
            review_status=review_status(value),human_acceptance='pending',edited_from=None)
        if stage_policy.asset_first(context):value['directing'].update(visual_policy=stage_policy.POLICY,capability_sha256=context['capability_sha256'])
        resources=verify_resources(value,check=ctx.check_active)
        ctx.check_active()
        saved=save_preview(local_root(),value)
        snapshot=ctx.store.put_json('SpineStagePreview',source.source_version_id,value)
        if snapshot.sha256!=saved['preview_sha256']:raise ProductionError('H5_PREVIEW_HASH_MISMATCH')
        provenance=dict(schema_version='performance_source/1.0',source_job_id=source.source_job_id,source_version_id=source.source_version_id,
            parent_job_id=source.parent_job_id,preview_sha256=snapshot.sha256,resources=resources,
            delivery_warnings=playback_view(job,ctx.store)['manifest'].get('delivery_warnings',[]),source_review_confirmed=False,
            casting_confirmed=True,model_review=value['directing']['review_status'])
        return StageResult(status='succeeded',source_version_id=source.source_version_id,technical_quality='passed',human_review='pending',
            artifacts=[snapshot,ctx.store.put_json('PerformanceSource',source.source_version_id,provenance)])


def review_status(value):
    if value.get('visual_policy')==stage_policy.POLICY:
        return 'passed_with_adaptations' if value.get('interpretation_notes') else 'passed'
    return 'passed_with_gaps' if value['diagnostics'] else 'passed'


def validate_directed_value(root,job,value,store):
    from .directing_edit import rebuild_edit
    meta=value['directing']
    if meta.get('edited_from'):
        base=read_directed_preview(root,job,meta['edited_from'],store)
        if base['directing'].get('edited_from'):raise ProductionError('DIRECTING_EDIT_CHAIN_INVALID')
        rebuilt=rebuild_edit(root,job,base,store,value)
    else:
        refs={k:ArtifactRef.model_validate(meta[k]) for k in ('context','plan','review','evidence')}
        if any(r.source_version_id!=job['source_version_id'] for r in refs.values()):raise ProductionError('SOURCE_VERSION_MISMATCH')
        if {k:r.kind for k,r in refs.items()}!={'context':'SpineDirectingContext','plan':'SpineDirectingPlan','review':'SpineDirectingReview','evidence':'SpineDirectingEvidence'}:raise ProductionError('DIRECTING_PROOF_INVALID')
        context=store.json(refs['context'])
        if context['source_job_id']!=job['id'] or context['source_version_id']!=job['source_version_id']:raise ProductionError('H5_SOURCE_BINDING_MISMATCH')
        review=parse_review(store.json(refs['review']));plan=store.json(refs['plan']);validate_review(review,context,plan)
        rebuilt,summary=compile_plan(job,store,context,plan,root)
        evidence=store.json(refs['evidence'])
        if evidence['execution']!=summary or evidence['context_sha256']!=refs['context'].sha256 or evidence['old_visual_tracks_supplied'] is not False:
            raise ProductionError('DIRECTING_PROOF_INVALID')
        expected_status=review_status(rebuilt)
        if stage_policy.asset_first(context) and (meta.get('visual_policy')!=stage_policy.POLICY or meta.get('capability_sha256')!=context['capability_sha256']):
            raise ProductionError('DIRECTING_PROOF_INVALID')
        if meta.get('review_status')!=expected_status or meta.get('human_acceptance')!='pending' or meta.get('casting_confirmed') is not True or meta.get('technical_status')!='passed':
            raise ProductionError('DIRECTING_PROOF_INVALID')
        rebuilt['directing']=meta
    if rebuilt!=value:raise ProductionError('H5_PREVIEW_REVALIDATION_MISMATCH')


def read_directed_preview(root,job,digest,store):
    import re
    if not re.fullmatch('[a-f0-9]{64}',digest):raise ProductionError('SPINE_PREVIEW_NOT_FOUND')
    try:
        raw=(root/'previews'/job['id']/(digest+'.json')).read_bytes();value=json.loads(raw)
        if sha256(raw).hexdigest()!=digest or value['job_id']!=job['id'] or value['schema_version']!='spine_stage_preview/3.2':raise ValueError()
        validate_directed_value(root,job,value,store)
    except (OSError,ValueError,KeyError,ValidationError,LifeValidationError):raise ProductionError('DIRECTING_PREVIEW_INVALID') from None
    return {**value,'preview_sha256':digest}
