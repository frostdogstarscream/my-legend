"""人物连续性回归：跨源片段留场、音频锚定入场和任意时间求值。"""
from copy import deepcopy
import unittest

from app.preview_integration.common import IntegrationError
from app.preview_integration.staging import apply_staging, build_schedule, sample


def fixtures():
    def block(identifier, kind, left, right, role=None):
        return {'block': {'block_id': identifier, 'type': kind, 'role_id': role,
                          'text': identifier}, 'start_ms': left, 'end_ms': right}
    scenes = [
        {'scene_id': 's1', 'start_ms': 0, 'end_ms': 700,
         'initial': {'a': {}, 'b': {}}, 'blocks': [block('b1', 'dialogue', 100, 600, 'a')]},
        {'scene_id': 's2', 'start_ms': 700, 'end_ms': 1900, 'initial': {'c': {}},
         'blocks': [block('b2', 'narration', 800, 1400), block('b3', 'dialogue', 1400, 1800, 'c')]},
    ]
    frames = []
    for time in range(0, 1901, 100):
        active = next((b['block'] for s in scenes for b in s['blocks']
                       if b['start_ms'] <= time < b['end_ms']), None)
        frames.append({'time_ms': time, 'scene_id': 's1' if time < 700 else 's2',
                       'block_id': active['block_id'] if active else None,
                       'speaker_id': active.get('role_id') if active else None,
                       'subtitle': active['text'] if active else '', 'fade': 0.8, 'actors': []})
    plan = {'plan_hash': 'aligned', 'manifest': {'duration_ms': 1900, 'scenes': scenes},
            'document': {'characters': [{'role_id': r} for r in 'abc'],
                         'scenes': [{'scene_id': 's1', 'cast': ['a', 'b'], 'background_type': 'indoor'},
                                    {'scene_id': 's2', 'cast': ['c'], 'background_type': 'indoor'}]},
            'frames': frames}
    config = {'schema_version': 'visual_staging/1.0', 'staging_id': 'test_v1',
              'groups': [{'group_id': 'room', 'scene_ids': ['s1', 's2'], 'cast': ['a', 'b', 'c'],
                          'initial_cast': ['a', 'b'], 'cues': [
                              {'cue_id': 'c_enter', 'block_id': 'b2', 'role_id': 'c',
                               'action': 'enter', 'duration_ms': 1000}]}]}
    return plan, config


def actors(frame):
    return {a['role_id']: a for a in frame['actors']}


class StagingTests(unittest.TestCase):
    def test_earlier_roles_persist_across_source_scene(self):
        plan, config = fixtures(); result = apply_staging(plan, config)
        before, after = actors(result['frames'][6]), actors(result['frames'][7])
        for role in ['a', 'b']:
            self.assertTrue(after[role]['visible'])
            self.assertEqual(before[role]['position'], after[role]['position'])
        self.assertFalse(after['c']['visible'])
        self.assertEqual(result['frames'][7]['fade'], 0)
        self.assertEqual(result['frames'][7]['scene_id'], 's2')

    def test_entry_uses_measured_anchor_and_fits_event(self):
        plan, config = fixtures(); result = apply_staging(plan, config)
        self.assertFalse(actors(result['frames'][7])['c']['visible'])
        self.assertEqual(actors(result['frames'][8])['c']['position'], [12, 108])
        self.assertEqual(actors(result['frames'][11])['c']['position'], [126, 108])
        self.assertEqual(actors(result['frames'][14])['c']['position'], [240, 108])
        cue = result['visual_staging']['groups'][0]['cues'][0]
        self.assertEqual((cue['start_ms'], cue['end_ms'], cue['effective_duration_ms']), (800, 1400, 600))
        self.assertEqual(result['manifest']['duration_ms'], plan['manifest']['duration_ms'])

    def test_seek_and_replay_are_deterministic(self):
        plan, config = fixtures(); _, groups = build_schedule(plan, config)
        expected = {t: sample(groups[0], t) for t in [0, 700, 800, 1100, 1400, 1700]}
        for t in [1700, 800, 0, 1100, 700, 1400, 1700]:
            self.assertEqual(sample(groups[0], t), expected[t])
        self.assertEqual(apply_staging(plan, config)['plan_hash'], apply_staging(plan, config)['plan_hash'])

    def test_source_events_and_aligned_plan_preserved(self):
        plan, config = fixtures(); snapshot = deepcopy(plan); result = apply_staging(plan, config)
        self.assertEqual(plan, snapshot)
        self.assertEqual(result['document'], plan['document'])
        for old, new in zip(plan['manifest']['scenes'], result['manifest']['scenes']):
            self.assertEqual(old['blocks'], new['blocks'])
        self.assertEqual(result['aligned_plan_hash'], 'aligned')
        self.assertNotEqual(result['plan_hash'], 'aligned')

    def test_new_visual_group_resets_state(self):
        plan, config = fixtures()
        config['groups'] = [
            {'group_id': 'first', 'scene_ids': ['s1'], 'cast': ['a', 'b'], 'initial_cast': ['a', 'b']},
            {'group_id': 'second', 'scene_ids': ['s2'], 'cast': ['c'], 'initial_cast': ['c']},
        ]
        result = apply_staging(plan, config)
        self.assertEqual(set(actors(result['frames'][7])), {'c'})
        self.assertEqual(actors(result['frames'][7])['c']['position'], [160, 108])

    def test_invalid_references_and_hidden_speaker_rejected(self):
        mutations = [
            lambda c: c['groups'][0]['cues'][0].update(block_id='missing'),
            lambda c: c['groups'][0]['cues'][0].update(role_id='missing'),
            lambda c: c['groups'][0]['scene_ids'].reverse(),
            lambda c: c['groups'][0].update(cues=[]),
            lambda c: c['groups'][0]['initial_cast'].append('c'),
            lambda c: c['groups'][0]['cues'][0].update(offset_ms=600),
            lambda c: c['groups'][0]['cues'][0].update(unknown_field=True),
        ]
        for mutate in mutations:
            plan, config = fixtures(); mutate(config)
            with self.subTest(config=config), self.assertRaises(IntegrationError):
                apply_staging(plan, config)

    def test_occupied_destination_rejected(self):
        plan, config = fixtures()
        config['groups'][0]['cues'].append({'cue_id': 'c_move', 'block_id': 'b3', 'role_id': 'c',
                                         'action': 'move', 'target_slot': 1, 'duration_ms': 100})
        with self.assertRaisesRegex(IntegrationError, '占用'):
            apply_staging(plan, config)

    def test_exit_hides_at_end_and_wait_preserves_pose(self):
        plan, config = fixtures()
        plan['manifest']['scenes'][1]['blocks'][1]['block'].update(type='narration', role_id=None)
        for frame in plan['frames']:
            if frame['time_ms'] >= 1400: frame['speaker_id'] = None
        config['groups'][0]['cues'].extend([
            {'cue_id': 'c_wait', 'block_id': 'b3', 'role_id': 'c', 'action': 'wait', 'duration_ms': 100},
            {'cue_id': 'c_exit', 'block_id': 'b3', 'role_id': 'c', 'action': 'exit', 'offset_ms': 100, 'duration_ms': 200},
        ])
        result = apply_staging(plan, config)
        self.assertEqual(actors(result['frames'][14])['c']['position'], [240, 108])
        self.assertTrue(actors(result['frames'][16])['c']['visible'])
        self.assertFalse(actors(result['frames'][17])['c']['visible'])
        self.assertTrue(actors(result['frames'][17])['a']['visible'])

    def test_conflicting_actions_and_visual_overlap_rejected(self):
        plan, config = fixtures()
        config['groups'][0]['cues'].append({'cue_id': 'a_wait', 'block_id': 'b2', 'role_id': 'a', 'action': 'wait'})
        with self.assertRaisesRegex(IntegrationError, '重叠'):
            apply_staging(plan, config)
        plan, config = fixtures(); plan['manifest']['scenes'][0]['blocks'][0]['block']['type'] = 'action'
        with self.assertRaisesRegex(IntegrationError, '两套'):
            apply_staging(plan, config)

    def test_optional_staging_leaves_plan_unchanged(self):
        plan, _ = fixtures(); result = apply_staging(plan)
        self.assertEqual(plan, result); self.assertIsNot(plan, result)

    def test_right_edge_entry_keeps_reserved_slot(self):
        plan, config = fixtures(); config['groups'][0]['cues'][0]['entry_side'] = 'right'
        result = apply_staging(plan, config)
        self.assertEqual(actors(result['frames'][8])['c']['position'], [308, 108])
        self.assertEqual(actors(result['frames'][11])['c']['position'], [274, 108])
        self.assertEqual(actors(result['frames'][14])['c']['position'], [240, 108])


if __name__ == '__main__':
    unittest.main()
