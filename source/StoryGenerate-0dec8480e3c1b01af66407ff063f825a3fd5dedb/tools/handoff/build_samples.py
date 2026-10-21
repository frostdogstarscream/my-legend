"""Render archived real stories without editing any story text or calling models."""
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs/handoff/samples"
P11 = ROOT / "6-实测产物/phase11_live_20261015_v3"
P10 = ROOT / "6-实测产物/operations_live_v8_20261014"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def canonical(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def link(path):
    return os.path.relpath(path, OUT).replace("\\", "/")


def metrics(calls):
    calls = list({c["call_id"]: c for c in calls}.values())
    reported = [c for c in calls if c.get("usage_source") == "reported"]
    return {"calls": len(calls), "input": sum(c.get("input_tokens", 0) for c in reported),
            "output": sum(c.get("output_tokens", 0) for c in reported),
            "reasoning_in_output": sum(c.get("reasoning_tokens", 0) for c in reported),
            "cache_read": sum(c.get("cache_read_tokens", 0) for c in reported),
            "model_seconds": round(sum(c.get("latency_ms", 0) for c in calls)/1000, 3),
            "models": sorted({c.get("actual_model", c.get("requested_model", "unknown")) for c in calls})}


def build():
    OUT.mkdir(parents=True, exist_ok=True)
    entries = []
    cases = [("S01", "Phase11 原创", P11/"create", True),
             ("S02", "Phase11 场景修订", P11/"revise", True),
             ("S03", "Phase11 新增两集", P11/"continue", True),
             ("S04", "Phase11 平行二创", P11/"remix", True),
             ("S05", "Phase10 台词修改的来源", P10/"round_1_create", False),
             ("S06", "Phase10 台词级修改", P10/"special_line", False),
             ("S07", "Phase10 再次续写至五集", P10/"special_continue_again", False)]
    for sample_id, label, folder, wrapped in cases:
        delivery = read(folder/"delivery.json")
        if wrapped:
            delivery = delivery["response"]
        request = read(folder/"request.json")
        plan_path = folder/"revised_plan.json" if (folder/"revised_plan.json").exists() else folder/"plan.json"
        plan = read(plan_path)
        if wrapped:
            plan = plan["response"]
        assert plan["plan_id"] == delivery["approved_plan_id"]
        assert plan["plan_revision"] == delivery["approved_plan_revision"]
        result = read(folder/"result.json")
        if wrapped:
            summary = read(P11/"summary.json")
            calls = [c for v in summary["resources"].values() if v["operation"] == request["intent"] for c in v["call_records"]]
            raw = P11/"model_calls"
        else:
            calls = read(folder/"calls.json")
            raw = folder/"model_calls"
        cost = metrics(calls)
        entry = {"id": sample_id, "label": label, "source_directory": str(folder.relative_to(ROOT)).replace("\\", "/"),
                 "delivery_path": str((folder/"delivery.json").relative_to(ROOT)).replace("\\", "/"),
                 "delivery_pointer": "response" if wrapped else "root",
                 "version_id": delivery["story_version_id"], "story_id": delivery["story_id"],
                 "parent": delivery["parent_story_version_id"], "version_no": delivery["version_no"],
                 "episodes": len(delivery["episodes"]), "content_sha256": canonical(delivery),
                 "body_human_edited": False, "cost": cost,
                 "operation_timing_fields": {k: v for k, v in result.items() if "sec" in k},
                 "sample_status": result["status"]}
        entries.append(entry)
        text = f"# {sample_id} {label}：《{delivery['title']}》\n\n"
        text += "正文直接逐行排版自归档 JSON，未人工润色、拼接或补写。评估意见另见质量报告。\n\n"
        text += f"版本 `{entry['version_id']}`；story_id `{entry['story_id']}`；版本号 {entry['version_no']}；父版本 `{entry['parent']}`；{entry['episodes']}集。\n\n"
        text += f"[完整机器 JSON]({link(folder/'delivery.json')})（读取位置：{entry['delivery_pointer']}）、[完整请求]({link(folder/'request.json')})、[批准所用Plan]({link(plan_path)})、[模型原始调用]({link(raw)})、[验收结果]({link(folder/'result.json')})。\n\n"
        text += f"用量（本操作归档范围含其失败/重试）：{cost['calls']}调用，Input {cost['input']:,}，Output {cost['output']:,}，其中reasoning {cost['reasoning_in_output']:,}已含于Output；缓存读{cost['cache_read']:,}；模型请求{cost['model_seconds']}秒；模型{', '.join(cost['models'])}。跨样例不得直接相加，继承正文也不代表该操作重新生成了全部剧集。\n\n"
        text += f"原记录耗时字段（秒）：`{json.dumps(entry['operation_timing_fields'], ensure_ascii=False)}`；获批Plan `{plan['plan_id']}` revision {plan['plan_revision']}。字段缺失不估算为零。\n\n"
        text += "## 真实用户输入\n\n> " + request["user_instruction"] + "\n\n## 角色\n\n|character_id|role_id|performer_id|Canon姓名|\n|---|---|---|---|\n"
        for actor in delivery["character_snapshot"]:
            text += f"|{actor['character_id']}|{actor['role_id']}|{actor.get('performer_id')}|{actor['canon'].get('name','')}|\n"
        text += "\n完整Canon、Runtime、剧情名和演绎标注均在上方JSON。旁白及虚构声音角色以JSON中的narrator_id/fictional_voice_roles为准。\n\n## 获批大纲\n\n"
        for episode in plan["plan"]["episode_outlines"]:
            text += f"- 第{episode['index']}集《{episode['title']}》：{episode['core_goal']}；节拍：{' → '.join(episode['major_beats'])}；结尾：{episode['ending']}。\n"
        text += "\n## 正式故事全文\n"
        for episode in delivery["episodes"]:
            text += f"\n### 第{episode['index']}集《{episode['title']}》\n\n"
            for scene in episode["scenes"]:
                text += f"\n#### {scene['title']}（{scene['scene_id']}）\n\n"
                for utterance in scene["utterances"]:
                    who = utterance.get("speaker_role_id") or utterance.get("narrator_id") or utterance["kind"]
                    text += f"**{who} / {utterance['kind']}** `{utterance['line_id']}`\n\n{utterance['text']}\n\n"
        text += "## 连续性状态（原始字段）\n\n```json\n" + json.dumps(delivery["continuity_state"], ensure_ascii=False, indent=2) + "\n```\n"
        (OUT/(sample_id+".md")).write_text(text, encoding="utf-8")
    (OUT/"index.json").write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    return entries


if __name__ == "__main__":
    print("Rendered", len(build()), "real archived stories; zero model calls")
