"""Bounded diagnostic projections. Never return a repaired or publishable plan."""
from copy import deepcopy
import json

from pydantic import ValidationError

from .directing_compile import compile_plan
from .directing_protocol import resolve_plan
from .life_contracts import LifeValidationError


TRACKS = ('actions', 'expressions', 'furniture')


def diagnose_plan(raw, context, job, store, root, *, max_passes=12):
    """Isolate bad cues on a private copy to expose subsequent repair diagnostics.

    Diagnostics after exclusions are explicitly provisional: removing a movement
    can change later state. No projection is returned, saved, reviewed as executed,
    or used for acceptance. The untouched model response must pass compile_plan.
    """
    result = dict(schema_version='spine_directing_diagnostics/1',
                  acceptance_allowed=False, passes=0, diagnostics=[], excluded=[])
    try:
        candidate = json.loads(raw) if isinstance(raw, str) else deepcopy(raw)
    except (ValueError, TypeError):
        result['stop_reason'] = 'invalid_json'
        return result
    if not isinstance(candidate, dict):
        result['stop_reason'] = 'invalid_root'
        return result
    excluded = []
    for index in range(max_passes):
        result['passes'] = index + 1
        try:
            plan = resolve_plan(candidate, context)
        except ValidationError as exc:
            removals = set(); extra_fields=set()
            for error in exc.errors(include_url=False, include_input=False, include_context=False):
                loc = error['loc']
                result['diagnostics'].append(dict(path='.'.join(map(str, loc)),
                    code='SCHEMA_INVALID', message=error['msg'],
                    provisional=bool(excluded), excluded_before=excluded[:]))
                # Only isolate complete cues; never guess a source, actor, scene,
                # duration, coordinate, classification, or other corrected value.
                if len(loc) >= 4 and loc[0] == 'beats' and isinstance(loc[1], int) and loc[2] in TRACKS and isinstance(loc[3], int):
                    removals.add(tuple(loc[:4]))
                elif len(loc) >= 2 and loc[0] in TRACKS and isinstance(loc[1], int):
                    removals.add(tuple(loc[:2]))
                elif len(loc)==1 and error['type']=='extra_forbidden':
                    extra_fields.add(loc[0])
            if not removals and not extra_fields:
                result['stop_reason'] = 'non_cue_schema_error'
                break
            for loc in sorted(removals, key=lambda x:tuple(str(p).zfill(8) if isinstance(p,int) else p for p in x), reverse=True):
                container = candidate
                try:
                    for part in loc[:-1]:container = container[part]
                    container.pop(loc[-1])
                except (KeyError, IndexError, TypeError):
                    result['stop_reason'] = 'unresolvable_schema_path'
                    return result
                excluded.append('.'.join(map(str,loc)))
            for field in sorted(extra_fields):
                candidate.pop(field,None);excluded.append(field)
            result['excluded'] = excluded[:]
            continue
        except LifeValidationError as exc:
            result['diagnostics'].extend(dict(d,provisional=bool(excluded),excluded_before=excluded[:]) for d in exc.diagnostics)
            result['stop_reason'] = 'source_or_structure_error'
            break
        try:
            _, summary = compile_plan(job, store, context, plan, root)
        except LifeValidationError as exc:
            result['diagnostics'].extend(dict(d,provisional=bool(excluded),excluded_before=excluded[:]) for d in exc.diagnostics)
            cues={c.id for track in TRACKS for c in getattr(plan,track)}
            bad={d['path'].split(':',1)[0] for d in exc.diagnostics} & cues
            if not bad:
                result['stop_reason'] = 'non_cue_execution_error'
                break
            candidate=plan.model_dump()
            for track in TRACKS:candidate[track]=[c for c in candidate[track] if c['id'] not in bad]
            for assessment in candidate['assessments']:
                assessment['cue_ids']=[cid for cid in assessment['cue_ids'] if cid not in bad]
                if assessment['classification']=='performed' and not assessment['cue_ids']:
                    assessment['classification']='gap'  # Diagnostic copy only.
            excluded.extend(sorted(bad));result['excluded']=excluded[:]
        else:
            result['stop_reason']='projection_checked' if excluded else 'original_checked'
            result['projection_issues']=summary['issues']
            break
    else:result['stop_reason']='diagnostic_limit'
    return result
