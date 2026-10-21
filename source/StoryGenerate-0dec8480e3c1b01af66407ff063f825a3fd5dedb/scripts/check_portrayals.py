"""Quick check of character portrayals in the experiment results."""
import json

with open("6-实测产物/acceptance/story_comparison_20261010_084532.json", "r", encoding="utf-8") as f:
    data = json.load(f)

for r in data:
    print(f"--- {r['label']} ---")
    plan = r.get("plan") or {}
    for cp in plan.get("character_portrayals", []):
        print(f"  role_id={cp['role_id']}  story_name={cp.get('story_name','')}  goal={str(cp.get('external_goal',''))[:50]}")
    for fvr in plan.get("fictional_voice_roles", []):
        print(f"  FVR role_id={fvr.get('role_id','')}  name={fvr.get('role_name','')}")
    print()