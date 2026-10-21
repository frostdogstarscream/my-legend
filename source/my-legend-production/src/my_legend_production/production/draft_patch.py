"""Source-bound line patches; titles, scene identities, and untouched lines stay fixed."""
from copy import deepcopy
import re

from pydantic import Field, create_model, model_validator
from .contracts import Contract


def patch_schema(draft, issues, contract_errors, target_schema=None):
    line_type = type(draft.scenes[0].lines[0])
    replacement = create_model('DraftLineReplacement', __base__=Contract,
        scene_index=(int, Field(ge=1)), line_index=(int, Field(ge=1)),
        original_text=(str, ...), line=(line_type, ...))
    addition = create_model('DraftLineAddition', __base__=Contract,
        scene_index=(int, Field(ge=1)), after_line_index=(int, Field(ge=0)), line=(line_type, ...))
    duplicate = create_model('DuplicateLineRemoval', __base__=Contract,
        scene_index=(int, Field(ge=1)), line_index=(int, Field(ge=1)), keep_line_index=(int, Field(ge=1)), original_text=(str, ...))
    global_issue = bool(contract_errors) or any(not x.startswith('scene:') for x in issues)
    affected = {(int(m[1]), int(m[2])) for x in issues
                if (m := re.match(r'scene:(\d+):line:(\d+):', x))}

    def valid(self):
        if not self.replacements and not self.additions and not self.remove_duplicates:
            raise ValueError('DRAFT_PATCH_EMPTY')
        seen = set()
        for item in self.replacements:
            key = (item.scene_index, item.line_index)
            if key in seen or not 1 <= item.scene_index <= len(draft.scenes):
                raise ValueError('DRAFT_PATCH_TARGET_INVALID')
            lines = draft.scenes[item.scene_index - 1].lines
            if not 1 <= item.line_index <= len(lines) or item.original_text != lines[item.line_index - 1].text:
                raise ValueError('DRAFT_PATCH_SOURCE_CHANGED')
            if not global_issue and key not in affected:
                raise ValueError('DRAFT_PATCH_UNRELATED_LINE')
            seen.add(key)
        removed = {(x.scene_index, x.line_index) for x in self.remove_duplicates}
        if len(removed) != len(self.remove_duplicates):
            raise ValueError('DRAFT_PATCH_DUPLICATE_REMOVAL')
        for item in self.remove_duplicates:
            key = (item.scene_index, item.line_index)
            if key in seen or key not in affected or not 1 <= item.scene_index <= len(draft.scenes):
                raise ValueError('DRAFT_PATCH_TARGET_INVALID')
            lines = draft.scenes[item.scene_index - 1].lines
            keep = (item.scene_index, item.keep_line_index)
            if not (1 <= item.line_index <= len(lines) and 1 <= item.keep_line_index <= len(lines)) or keep in removed or keep in seen:
                raise ValueError('DRAFT_PATCH_DUPLICATE_NOT_PRESERVED')
            if lines[item.line_index - 1].text != item.original_text or lines[item.keep_line_index - 1].text != item.original_text:
                raise ValueError('DRAFT_PATCH_DUPLICATE_NOT_PRESERVED')
        for item in self.additions:
            if not 1 <= item.scene_index <= len(draft.scenes) or item.after_line_index > len(draft.scenes[item.scene_index - 1].lines):
                raise ValueError('DRAFT_PATCH_TARGET_INVALID')
            if not global_issue and (item.scene_index, item.after_line_index) not in affected:
                raise ValueError('DRAFT_PATCH_UNRELATED_LINE')
        if target_schema is not None:
            apply_patch(draft, self, target_schema)
        return self
    return create_model('StoryLinePatch', __base__=Contract,
        replacements=(list[replacement], Field(max_length=1000)), additions=(list[addition], Field(max_length=1000)),
        remove_duplicates=(list[duplicate], Field(default_factory=list, max_length=1000)),
        __validators__={'source_binding': model_validator(mode='after')(valid)})


def apply_patch(draft, patch, schema):
    data = deepcopy(draft.model_dump(mode='json'))
    replacements = {(x.scene_index, x.line_index): x.line.model_dump(mode='json') for x in patch.replacements}
    for si, scene in enumerate(data['scenes'], 1):
        lines = []
        for index in range(len(scene['lines']) + 1):
            if index and not any((x.scene_index, x.line_index) == (si, index) for x in patch.remove_duplicates):
                lines.append(replacements.get((si, index), scene['lines'][index - 1]))
            lines.extend(x.line.model_dump(mode='json') for x in patch.additions if (x.scene_index, x.after_line_index) == (si, index))
        scene['lines'] = lines
    return schema.model_validate(data)
