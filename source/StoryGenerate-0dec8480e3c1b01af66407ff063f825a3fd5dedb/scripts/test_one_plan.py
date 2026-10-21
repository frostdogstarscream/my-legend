"""Single-plan test with long timeout."""
import os, sys, json
sys.path.insert(0, '4-引擎代码')

if not os.environ.get("DRAMA_LLM_API_KEY"):
    raise SystemExit("Set DRAMA_LLM_API_KEY in the process environment before running this script.")
os.environ.setdefault("DRAMA_LLM_MODEL", "qwen/qwen3.7-max")

from drama_engine.llm.openai_compat import OpenAICompatProvider
from drama_engine.llm.router import resolve_spec, ModelTierMap, TIER_STRONG
from drama_engine.workflow.schemas import (
    CreationPreferences, CreateStoryRequest, StoryModeChoice,
    CharacterSelection, OperationContext,
)
from drama_engine.workflow.adapters import LLMPlanGenerator
from drama_engine.workflow.service import StoryWorkflowService
from drama_engine.workflow.repository import InMemoryWorkflowRepository
from drama_engine.templates.repository import MemoryStoryTemplateRepository
from drama_engine.templates.resolver import StoryTemplateResolver

provider = OpenAICompatProvider(
    api_key=os.environ["DRAMA_LLM_API_KEY"],
    base_url="https://api.deepseek.com/v1",
    read_timeout=180.0,
    max_retries=2,
)
tier_map = ModelTierMap(mapping={TIER_STRONG: os.environ["DRAMA_LLM_MODEL"]})

import drama_engine.llm.router as rm
_orig = rm.resolve_spec
rm.resolve_spec = lambda role, **ov: _orig(role, tier_map=tier_map, **ov)

plan_gen = LLMPlanGenerator(provider)

class NoopExecutor:
    def execute(self, snapshot):
        raise NotImplementedError

service = StoryWorkflowService(
    InMemoryWorkflowRepository(), plan_gen, NoopExecutor(),
    template_resolver=StoryTemplateResolver(MemoryStoryTemplateRepository()),
)

idea = (
    "三十五岁的全职主妇林敏，结婚八年，每天在婆婆挑剔和丈夫冷漠中度过。"
    "她发现丈夫出轨后，悄悄收集证据，重新捡起手工皮具生意，"
    "最终在家庭聚会上摊牌离婚，展示自己新工作室的钥匙。"
)

cases = [
    ("AUTO", CreationPreferences(story_mode=StoryModeChoice.AUTO, genre="auto", target_duration_sec=180, target_episodes=1)),
    ("R4_family", CreationPreferences(story_mode=StoryModeChoice.GENERAL, genre="G01", recipe_id="R4", target_duration_sec=180, target_episodes=1)),
    ("R5_healing", CreationPreferences(story_mode=StoryModeChoice.GENERAL, genre="G02", recipe_id="R5", target_duration_sec=180, target_episodes=1)),
    ("R3_business", CreationPreferences(story_mode=StoryModeChoice.VIRAL_DRAMA, genre="G08", recipe_id="R3", target_duration_sec=180, target_episodes=1)),
]

results = []

for label, prefs in cases:
    print(f"\n--- {label} ---")
    try:
        req = CreateStoryRequest(
            request_id=label,
            idempotency_key=label,
            user_instruction=idea,
            creation_preferences=prefs,
            characters=CharacterSelection(
                selected_character_ids=["protagonist"],
                role_bindings=[{"character_id": "protagonist", "story_role_id": "protagonist", "performer_id": "protagonist"}],
            ),
        )
        ctx = OperationContext(character_snapshots={
            "protagonist": {
                "canon": {"name": "林敏", "immutable_facts": ["三十五岁", "全职主妇", "手工皮具手艺"]},
                "profile": {"goal": "掌控自己的人生", "motivation": "不再忍让", "fear": "失去抚养权"},
                "profile_revision": 1,
            },
        })
        plan = service.prepare_story_plan(req, ctx)
        p = plan.plan
        s = plan.strategy_snapshot or {}
        result = {
            "label": label,
            "title": p.title,
            "premise": p.premise,
            "theme": p.theme,
            "beginning": p.beginning,
            "development": p.development,
            "climax": p.climax,
            "ending": p.ending,
            "episode_outlines": [o.model_dump(mode="json") for o in p.episode_outlines],
            "character_portrayals": [c.model_dump(mode="json") for c in p.character_portrayals],
            "strategy": s,
            "mode": s.get("story_mode"),
            "genre": s.get("genre_id"),
            "recipe": s.get("recipe_id"),
            "conflict_mechanism": s.get("conflict_mechanism", ""),
            "causal_driver": s.get("causal_driver", ""),
            "ending_strategy": s.get("ending_strategy", ""),
            "tone_guidance": s.get("tone_guidance", ""),
        }
        results.append(result)
        print(f"  [OK] {p.title}")
        print(f"  [OK] mode={s.get('story_mode')} genre={s.get('genre_id')} recipe={s.get('recipe_id')}")
        print(f"  [OK] conflict: {s.get('conflict_mechanism', '')[:100]}")
    except Exception as exc:
        print(f"  [ERR] {type(exc).__name__}: {exc}")
        results.append({"label": label, "error": str(exc)})

# Save
out_dir = "6-实测产物/strategy_compare_plans"
os.makedirs(out_dir, exist_ok=True)
ts = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y%m%d_%H%M%S")
path = os.path.join(out_dir, f"plan_compare_{ts}.json")
with open(path, "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print(f"\nSaved: {path}")

# Print comparison
for r in results:
    print(f"\n{'='*60}")
    print(f"  {r['label']}")
    if "error" in r:
        print(f"  ERROR: {r['error']}")
        continue
    print(f"  Mode: {r['mode']}  Genre: {r['genre']}  Recipe: {r['recipe']}")
    print(f"  Title: {r['title']}")
    print(f"  Theme: {r['theme']}")
    print(f"  Conflict: {r['conflict_mechanism'][:100]}")
    print(f"  Causal: {r['causal_driver'][:100]}")
    print(f"  Ending: {r['ending_strategy'][:100]}")
    print(f"  Portrayals: {[p['story_name'] + ':' + p.get('external_goal','')[:30] for p in r.get('character_portrayals', [])]}")