"""Strategy impact analysis — deterministic, no API calls."""
import json, sys
sys.path.insert(0, '4-引擎代码')
from drama_engine.config import RuleLibrary
from drama_engine.workflow.strategy import StoryStrategyResolver

lib = RuleLibrary.load()
r = StoryStrategyResolver(lib)
idea = '三十五岁全职主妇林敏发现丈夫出轨，悄悄收集证据重新创业，最终在家庭聚会摊牌离婚'

cases = [
    ('AUTO', 'auto', 'auto', None, []),
    ('GENERAL_no_recipe', 'general', 'auto', None, ['story_mode']),
    ('R4_family_revenge', 'general', 'G01', 'R4', ['story_mode', 'genre', 'recipe_id']),
    ('R5_healing_romance', 'general', 'G02', 'R5', ['story_mode', 'genre', 'recipe_id']),
    ('R3_business_comeback', 'viral_drama', 'G08', 'R3', ['story_mode', 'genre', 'recipe_id']),
    ('R1_palace_upgrade', 'viral_drama', 'G04', 'R1', ['story_mode', 'genre', 'recipe_id']),
]

print('=' * 70)
print('STRATEGY COMPARISON: Same Idea, Different Recipes')
print(f'Idea: {idea[:60]}...')
print('=' * 70)

for label, mode, genre, recipe, explicit in cases:
    s = r.resolve(
        story_mode=mode, genre=genre, recipe_id=recipe,
        user_instruction=idea, explicit_fields=explicit,
    )
    print(f'\n--- {label} ---')
    print(f'  Mode: {s.story_mode} ({s.mode_label})')
    print(f'  Genre: {s.genre_id} ({s.genre_name})')
    print(f'  Recipe: {s.recipe_id} ({s.recipe_name})')
    print(f'  Auto-filled: {s.auto_filled_fields}')
    print(f'  Conflict: {s.conflict_mechanism[:150]}')
    print(f'  Causal:   {s.causal_driver[:150]}')
    print(f'  Ending:   {s.ending_strategy[:150]}')

# Show plan_context differences between R4 and R5
print('\n' + '=' * 70)
print('PLAN CONTEXT: R4 vs R5 (injected into Plan system prompt)')
print('=' * 70)

r4 = r.resolve(story_mode='general', genre='G01', recipe_id='R4',
               user_instruction=idea, explicit_fields=['story_mode', 'genre', 'recipe_id'])
r5 = r.resolve(story_mode='general', genre='G02', recipe_id='R5',
               user_instruction=idea, explicit_fields=['story_mode', 'genre', 'recipe_id'])

print('\n-- R4 (family revenge) plan_context:')
print(r4.plan_context())
print('\n-- R5 (healing romance) plan_context:')
print(r5.plan_context())

# Show writer hints
print('\n' + '=' * 70)
print('WRITER HINTS: R4 vs R5 (injected via creative_packet.strategy_hints)')
print('=' * 70)
print(f'\nR4 hints: {r4.writer_context()}')
print(f'\nR5 hints: {r5.writer_context()}')

# Save comparison data
results = []
for label, mode, genre, recipe, explicit in cases:
    s = r.resolve(story_mode=mode, genre=genre, recipe_id=recipe,
                  user_instruction=idea, explicit_fields=explicit)
    results.append({
        'label': label,
        'mode': s.story_mode, 'genre': s.genre_id, 'recipe': s.recipe_id,
        'genre_name': s.genre_name, 'recipe_name': s.recipe_name,
        'conflict_mechanism': s.conflict_mechanism,
        'causal_driver': s.causal_driver,
        'ending_strategy': s.ending_strategy,
        'tone_guidance': s.tone_guidance,
        'auto_filled': s.auto_filled_fields,
        'plan_context': s.plan_context(),
    })

import os
os.makedirs('6-实测产物/strategy_impact', exist_ok=True)
path = '6-实测产物/strategy_impact/strategy_comparison_deterministic.json'
with open(path, 'w', encoding='utf-8') as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print(f'\nSaved: {path}')