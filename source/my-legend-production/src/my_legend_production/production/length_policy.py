"""Local soft guidance and generous hard guards, not supplier/API size limits."""
import math
from pydantic import create_model, model_validator

VERSION = 'story_audio_length/1.1'
DURATION_HARD_BOUNDS = (0.3, 1.6)
INSTRUCTION_LIMITS = {'soft': 360, 'hard': 600}


def spoken_budget(draft, target_seconds):
    ideal = round(target_seconds * 3.5)
    current = sum(len(line.text) for scene in draft.scenes for line in scene.lines
                  if line.kind in ('dialogue', 'narration'))
    low, high = math.ceil(ideal * DURATION_HARD_BOUNDS[0]), math.floor(ideal * DURATION_HARD_BOUNDS[1])
    soft_low, soft_high = math.ceil(ideal * .8), math.ceil(ideal * 1.3)
    return {'policy_version': VERSION, 'min': low, 'ideal': ideal, 'max': high,
        'recommended_min': soft_low, 'soft_max': soft_high, 'current': current,
        'add_at_least': max(0, low-current), 'remove_at_least': max(0, current-high),
        'soft_warning': current < soft_low or current > soft_high,
        'hard_violation': not low <= current <= high,
        'duration_basis': 'character_estimate_not_measured_audio',
        'counted_fields': 'dialogue/narration text only, including punctuation'}


def contract_errors(draft, target_seconds, roles):
    from drama_engine.workflow.adapters import draft_episode_contract_errors
    return draft_episode_contract_errors(draft, target_seconds, roles, duration_bounds=DURATION_HARD_BOUNDS)


def repair_schema(schema, target_seconds, roles):
    def validate(self):
        from drama_engine.workflow.adapters import normalize_draft_stage_directions
        normalized = normalize_draft_stage_directions(self)
        _, errors = contract_errors(normalized, target_seconds, roles)
        if errors:
            raise ValueError('STORY_DRAFT_REPAIR_CONTRACT_INVALID: ' + str(errors) + '; budget=' + str(spoken_budget(normalized, target_seconds)))
        return self
    return create_model('FlexibleStoryAudioRepair', __base__=schema,
        __validators__={'production_contract': model_validator(mode='after')(validate)})
