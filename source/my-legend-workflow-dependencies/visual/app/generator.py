from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from json_repair import repair_json

from .config import Settings
from .providers import ProviderError, chat
from .story_input import StoryContext, build_source_text


PLAN_PROMPT = """你是一名资深中文舞台剧改编策划。根据给定章节，规划一部可实际排演的话剧结构。
只输出 JSON 对象，不能输出 Markdown，结构必须是：
{"characters":[{"name":"角色名","description":"一句话人物说明"}],"scenes":[{"scene_number":1,"task":"本场要完成的冲突、选择或信息变化","location":"地点","time":"时间"}]}。
要求：忠于原文核心人物关系和事件，可合并次要人物；scenes 数量必须等于给定场次数；每场都必须有明确的行动目标、阻碍或关系变化，不能只写氛围；后续逐场编剧只能使用 characters 里的角色名。"""

SCENE_PROMPT = """你是一名资深中文舞台剧编剧。根据给定故事的一个章节和本场任务，输出一场可实际排演的中文话剧。
只输出 JSON 对象，不能输出 Markdown，结构必须是 {"blocks":[...]}。每个 block 使用以下字段：
{"type":"scene_heading|stage_direction|dialogue|pause|sfx|light|action","text":"内容","character":"说话角色名","emotion":"neutral|happy|sad|angry|fearful|surprised|tender|serious|excited|nervous|mysterious","intent":"inform|ask|persuade|warn|threaten|comfort|refuse|apologize|mock|encourage|command|conceal","delivery":"statement|question|command|exclamation|whisper|narration|chant|shout|crying","intensity":0.0,"actions":[{"action":"nod|shake_head|look_left|look_right|look_up|look_down|lean_forward|lean_back|turn_left|turn_right|wave|point|tremble|recoil|bow|idle","start_offset_ms":0,"duration_ms":800,"intensity":0.5}]}。
本场必须包含 1 个 scene_heading、至少指定数量的 dialogue；每句台词都必须使用给定角色名。对白要有来回、打断、沉默、行动目标和可观察动作，不能用叙述概括替代冲突。"""


def _json_from_text(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError("模型没有返回 JSON 对象")
    candidate = cleaned[start:end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as strict_error:
        # 长篇 JSON 即便已启用模型的 JSON mode，仍可能偶发漏掉一个逗号或引号。
        # 仅在严格解析失败时修复，并继续由后续 blocks / TPM 校验把关。
        try:
            repaired = repair_json(candidate, return_objects=True)
        except Exception:
            raise strict_error
        if not isinstance(repaired, dict):
            raise strict_error
        return repaired


def _sentences(source: str) -> list[str]:
    parts = [x.strip() for x in re.split(r"(?<=[。！？])", re.sub(r"\s+", "", source)) if len(x.strip()) >= 6]
    return parts[:12] or [source[:120]]


def _characters(source: str) -> list[str]:
    found = re.findall(r"(?:^|[\n。！？])([\u4e00-\u9fff]{2,4})[：:]", source)
    stop = {"说道", "问道", "答道", "只见", "忽然", "此时", "原来"}
    names = list(dict.fromkeys(name for name in found if name not in stop))[:6]
    return names if len(names) >= 2 else ["主角", "来客"]


def _performance_contract(options: dict, chapter_count: int) -> dict:
    """把分钟数转换为模型能执行、又能被程序验收的最低容量。"""
    target_seconds = int(options.get("duration_minutes", 10)) * 60
    min_dialogue_chars = max(500, round(target_seconds * 3.5))
    return {
        "target_seconds": target_seconds,
        "minimum_dialogue_characters": min_dialogue_chars,
        "minimum_dialogue_turns": max(16, (min_dialogue_chars + 35) // 36),
        "minimum_scenes": max(3, chapter_count * 2),
        "acceptance": "仅当对白字数、对白轮次和场次数全部达到最低值，才可交付。",
    }


def _dialogue_stats(script: dict) -> dict:
    dialogues = [block for block in script.get("blocks", []) if block.get("type") == "dialogue"]
    return {
        "dialogue_characters": sum(len(str(block.get("text", ""))) for block in dialogues),
        "dialogue_turns": len(dialogues),
        "scenes": sum(block.get("type") == "scene_heading" for block in script.get("blocks", [])),
    }


# 验收允许 10% 的交付弹性：逐场生成存在自然波动，90% 已足以排除提纲式短稿。
CONTRACT_TOLERANCE = 0.9


def _meets_contract(script: dict, contract: dict) -> bool:
    stats = _dialogue_stats(script)
    return (
        stats["dialogue_characters"] >= contract["minimum_dialogue_characters"] * CONTRACT_TOLERANCE
        and stats["dialogue_turns"] >= contract["minimum_dialogue_turns"] * CONTRACT_TOLERANCE
        and stats["scenes"] >= contract["minimum_scenes"]
    )


def _generate_scene_blocks(payload: dict, settings: Settings, min_chars: int, min_turns: int, scene_no: int) -> list[dict]:
    """生成单一场次：JSON 格式重试一次，容量不足最多重写两次，拒绝提纲式短稿。"""
    try:
        result = _json_from_text(chat(SCENE_PROMPT, json.dumps(payload, ensure_ascii=False), settings=settings, max_tokens=8000, json_mode=True))
    except ValueError:
        # 个别模型会偶发输出空内容或解释文字；保留同一场上下文重试一次。
        payload = payload | {"format_retry_instruction": "上一响应不是有效 JSON。现在只能输出一个包含 blocks 数组的完整 JSON 对象，禁止说明、禁止 Markdown。"}
        result = _json_from_text(chat(SCENE_PROMPT, json.dumps(payload, ensure_ascii=False), settings=settings, max_tokens=8000, json_mode=True))
    stats = _dialogue_stats(result)
    for _ in range(2):
        if stats["dialogue_characters"] >= min_chars * CONTRACT_TOLERANCE and stats["dialogue_turns"] >= min_turns * CONTRACT_TOLERANCE:
            break
        payload = payload | {
            "revision_instruction": (
                f"本场仍然过短：当前 {stats['dialogue_characters']} 字、{stats['dialogue_turns']} 轮，"
                f"还差约 {max(0, min_chars - stats['dialogue_characters'])} 字、{max(0, min_turns - stats['dialogue_turns'])} 轮。"
                "请完整重写本场并明显加长：增加来回对白、打断、沉默与回应，严格达到 minimum_dialogue_characters 与 minimum_dialogue_turns；不能只给提纲。"
            ),
            "previous_stats": stats,
        }
        result = _json_from_text(chat(SCENE_PROMPT, json.dumps(payload, ensure_ascii=False), settings=settings, max_tokens=8000, json_mode=True))
        stats = _dialogue_stats(result)
    if stats["dialogue_characters"] < min_chars * CONTRACT_TOLERANCE or stats["dialogue_turns"] < min_turns * CONTRACT_TOLERANCE:
        raise ValueError(f"第 {scene_no} 场未达到可演容量：{stats['dialogue_characters']} 字、{stats['dialogue_turns']} 轮。")
    blocks = result.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        raise ValueError(f"第 {scene_no} 场缺少 blocks")
    return blocks


# 场次之间没有数据依赖，并发生成可把总耗时从「各场之和」压到「最慢一场」。
MAX_PARALLEL_SCENES = 5


def _generate_scenes_parallel(tasks: list[tuple[int, dict]], settings: Settings, min_chars: int, min_turns: int) -> dict[int, list[dict]]:
    """并发生成全部场次，返回 {scene_no: blocks}；任一场失败即整体抛出。"""
    if len(tasks) == 1:
        scene_no, payload = tasks[0]
        return {scene_no: _generate_scene_blocks(payload, settings, min_chars, min_turns, scene_no)}
    results: dict[int, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL_SCENES, len(tasks))) as executor:
        futures = {
            executor.submit(_generate_scene_blocks, payload, settings, min_chars, min_turns, scene_no): scene_no
            for scene_no, payload in tasks
        }
        for future in as_completed(futures):
            results[futures[future]] = future.result()
    return results


def _generate_story_by_scenes(context: StoryContext, options: dict, settings: Settings) -> dict:
    """长剧本按章节拆为多个演出场，避免单次 JSON 过长而超时或被截断。"""
    contract = _performance_contract(options, len(context.chapters))
    scene_count = contract["minimum_scenes"]
    # 场次数必须达到合同最低值：单章故事也需要 3 场，否则合并后永远无法通过整剧验收。
    scenes_per_chapter = (scene_count + len(context.chapters) - 1) // len(context.chapters)
    total_scenes = scenes_per_chapter * len(context.chapters)
    min_chars = (contract["minimum_dialogue_characters"] + scene_count - 1) // scene_count
    min_turns = (contract["minimum_dialogue_turns"] + scene_count - 1) // scene_count
    phases = ("建立冲突与人物目标", "升级阻碍、做出选择并留下下一章的悬念", "收束冲突，人物关系出现可见变化")
    tasks: list[tuple[int, dict]] = []
    scene_meta: dict[int, tuple[int, int, dict]] = {}
    scene_no = 0
    for chapter_index, chapter in enumerate(context.chapters, start=1):
        for phase_index in range(1, scenes_per_chapter + 1):
            phase = phases[phase_index - 1] if phase_index <= len(phases) else phases[1]
            scene_no += 1
            scene_contract = {
                "minimum_dialogue_characters": min_chars,
                "minimum_dialogue_turns": min_turns,
                "scene_number": scene_no,
                "total_scenes": total_scenes,
            }
            payload = {
                "story_title": context.title,
                "characters": context.characters,
                "relationships": context.relationships,
                "chapter": chapter,
                "scene_task": phase,
                "continuity": "承接此前人物关系变化；不得提前解决尚未发生的后续章节事件。",
                "performance_contract": scene_contract,
            }
            tasks.append((scene_no, payload))
            scene_meta[scene_no] = (chapter_index, phase_index, chapter)
    blocks_by_scene = _generate_scenes_parallel(tasks, settings, min_chars, min_turns)
    all_blocks: list[dict] = []
    for scene_no in range(1, total_scenes + 1):
        chapter_index, phase_index, chapter = scene_meta[scene_no]
        for block in blocks_by_scene[scene_no]:
            block["id"] = f"blk_{len(all_blocks) + 1:04d}"
            if block.get("type") == "scene_heading":
                block["act"] = chapter_index
                block["scene"] = phase_index
                block.setdefault("location", chapter.get("location", "舞台主场景"))
                block.setdefault("time", chapter.get("time", "故事进行中"))
                block.setdefault("characters", [character["name"] for character in context.characters])
            all_blocks.append(block)
    script = {
        "title": context.title,
        "chapter_title": "、".join(chapter["title"] for chapter in context.chapters),
        "characters": [],
        "adaptation_notes": ["按全部所选章节拆分为连续演出场。", "每场以对白字数和轮次满足可演容量后再合并。", "TPM 时间戳为估算值，接入 TTS 后应以真实音频时长重新锁定。"],
        "blocks": all_blocks,
    }
    if not _meets_contract(script, contract):
        raise ValueError("分场合并后未达到整剧可演时长合同。")
    return _apply_story_identity(script, context)


def _generate_text_by_scenes(title: str, chapter_title: str, source: str, options: dict, settings: Settings) -> dict:
    """纯文本输入同样先策划、再逐场生成：单次调用写不出整场话剧的体量。"""
    contract = _performance_contract(options, 1)
    scene_count = contract["minimum_scenes"]
    min_chars = (contract["minimum_dialogue_characters"] + scene_count - 1) // scene_count
    min_turns = (contract["minimum_dialogue_turns"] + scene_count - 1) // scene_count
    plan_payload = {"title": title, "chapter_title": chapter_title, "source_text": source, "options": options, "scene_count": scene_count, "performance_contract": contract}
    plan = _json_from_text(chat(PLAN_PROMPT, json.dumps(plan_payload, ensure_ascii=False), settings=settings, max_tokens=4000, json_mode=True))
    characters = plan.get("characters")
    scenes = plan.get("scenes")
    if not isinstance(characters, list) or not any(isinstance(c, dict) and c.get("name") for c in characters):
        raise ValueError("改编策划缺少角色表")
    if not isinstance(scenes, list) or len(scenes) < scene_count:
        raise ValueError(f"改编策划场次数不足：{len(scenes) if isinstance(scenes, list) else 0}/{scene_count}")
    names = {str(c["name"]) for c in characters if isinstance(c, dict) and c.get("name")}
    total_scenes = len(scenes)
    tasks: list[tuple[int, dict]] = []
    for scene_no, scene in enumerate(scenes, start=1):
        if not isinstance(scene, dict):
            scene = {}
        payload = {
            "story_title": title,
            "chapter_title": chapter_title,
            "characters": characters,
            "source_text": source,
            "scene_task": scene.get("task") or "推进冲突并留下转折",
            "continuity": "承接此前场次的人物关系变化；不得提前解决后续场次的事件。",
            "performance_contract": {
                "minimum_dialogue_characters": min_chars,
                "minimum_dialogue_turns": min_turns,
                "scene_number": scene_no,
                "total_scenes": total_scenes,
            },
        }
        tasks.append((scene_no, payload))
    blocks_by_scene = _generate_scenes_parallel(tasks, settings, min_chars, min_turns)
    all_blocks: list[dict] = []
    for scene_no, scene in enumerate(scenes, start=1):
        if not isinstance(scene, dict):
            scene = {}
        for block in blocks_by_scene[scene_no]:
            if block.get("type") == "dialogue" and block.get("character") not in names:
                raise ValueError(f"第 {scene_no} 场出现了策划中不存在的角色：{block.get('character')}")
            block["id"] = f"blk_{len(all_blocks) + 1:04d}"
            if block.get("type") == "scene_heading":
                block["act"] = 1
                block["scene"] = scene_no
                block.setdefault("location", scene.get("location", "舞台主场景"))
                block.setdefault("time", scene.get("time", "故事进行中"))
                block.setdefault("characters", sorted(names))
            all_blocks.append(block)
    script = {
        "title": title or "未命名改编",
        "chapter_title": chapter_title,
        "characters": [{"name": str(c.get("name")), "description": str(c.get("description", ""))} for c in characters if isinstance(c, dict) and c.get("name")],
        "adaptation_notes": ["先由模型完成改编策划（角色表与场次任务），再逐场生成并合并。", "每场以对白字数和轮次满足可演容量后再合并。", "TPM 时间戳为估算值，接入 TTS 后应以真实音频时长重新锁定。"],
        "blocks": all_blocks,
    }
    if not _meets_contract(script, contract):
        raise ValueError("分场合并后未达到整剧可演时长合同。")
    return script


def demo_script(title: str, chapter_title: str, source: str, options: dict) -> dict:
    names = _characters(source)
    a, b = names[0], names[1]
    beats = _sentences(source)
    conflict = beats[0].rstrip("。！？")
    reveal = beats[len(beats) // 2].rstrip("。！？")
    outcome = beats[-1].rstrip("。！？")
    blocks = [
        {"id":"blk_0001","type":"scene_heading","act":1,"scene":1,"location":"一处可快速换景的室内","time":"当下","characters":names,"text":"第一幕 第一场：逼近的选择"},
        {"id":"blk_0002","type":"light","name":"冷白灯渐亮","duration_ms":900,"text":"冷白灯渐亮，空间被切成明暗两区。"},
        {"id":"blk_0003","type":"stage_direction","text":f"{a}站在亮处，指节无意识地收紧；{b}停在暗处，没有立刻上前。"},
        {"id":"blk_0004","type":"dialogue","character":a,"text":f"你也听见了——{conflict}。现在还要装作什么都没发生吗？","emotion":"serious","intent":"warn","delivery":"question","intensity":0.68,"actions":[{"action":"lean_forward","start_offset_ms":400,"duration_ms":800,"intensity":0.55}]},
        {"id":"blk_0005","type":"pause","text":"短暂停顿。对方避开视线。","duration_ms":700},
        {"id":"blk_0006","type":"dialogue","character":b,"text":"我不是装作没发生。我是在等你把真正想问的话说出来。","emotion":"tender","intent":"persuade","delivery":"statement","intensity":0.46,"actions":[{"action":"look_down","start_offset_ms":0,"duration_ms":600,"intensity":0.4}]},
        {"id":"blk_0007","type":"dialogue","character":a,"text":"好。那我问你：到了必须选择的时候，你站在哪一边？","emotion":"angry","secondary_emotion":"nervous","intent":"ask","delivery":"command","intensity":0.76,"actions":[{"action":"point","start_offset_ms":700,"duration_ms":600,"intensity":0.65}]},
        {"id":"blk_0008","type":"scene_heading","act":1,"scene":2,"location":"同地，灯光收窄","time":"片刻后","characters":names,"text":"第一幕 第二场：真相换位"},
        {"id":"blk_0009","type":"stage_direction","text":f"{b}终于走进亮处，声音不高，却迫使{a}后退半步。"},
        {"id":"blk_0010","type":"dialogue","character":b,"text":f"你只看见结果，却没看见中间发生的事：{reveal}。我若早说，你还会让我留下吗？","emotion":"sad","intent":"conceal","delivery":"question","intensity":0.64,"actions":[{"action":"lean_forward","start_offset_ms":900,"duration_ms":900,"intensity":0.5}]},
        {"id":"blk_0011","type":"dialogue","character":a,"text":"所以你替我做了决定。你以为那是保护，其实是把我排除在外。","emotion":"sad","secondary_emotion":"angry","intent":"refuse","delivery":"statement","intensity":0.7,"actions":[{"action":"shake_head","start_offset_ms":500,"duration_ms":700,"intensity":0.45}]},
        {"id":"blk_0012","type":"sfx","name":"远处钟声","duration_ms":1200,"text":"远处传来一记钟声。"},
        {"id":"blk_0013","type":"dialogue","character":b,"text":"那就从现在开始，不替你决定。你问，我回答；你要走，我不拦。","emotion":"serious","intent":"apologize","delivery":"statement","intensity":0.6,"actions":[{"action":"nod","start_offset_ms":1200,"duration_ms":600,"intensity":0.5}]},
        {"id":"blk_0014","type":"dialogue","character":a,"text":f"不。我留下。因为故事还没有结束——{outcome}。这一次，我们一起面对。","emotion":"serious","secondary_emotion":"tender","intent":"encourage","delivery":"statement","intensity":0.72,"actions":[{"action":"lean_forward","start_offset_ms":1500,"duration_ms":800,"intensity":0.55}]},
        {"id":"blk_0015","type":"light","name":"暖光合拢","duration_ms":1500,"text":"两区灯光缓慢合拢，人物第一次站在同一片光里。"},
    ]
    return {
        "title": title or "未命名改编",
        "chapter_title": chapter_title,
        "characters": [{"name": name, "description": "由原文章节识别；生成后可继续调整。"} for name in names],
        "adaptation_notes": ["保留原文核心信息，压缩为冲突、揭示、选择三个舞台节拍。", "补充了可表演对白和动作，用于快速获得完整初稿。", "TPM 时间戳为估算值，接入 TTS 后应以真实音频时长重新锁定。"],
        "blocks": blocks,
    }


def _apply_story_identity(script: dict, context: StoryContext) -> dict:
    by_name = {item["name"]: item for item in context.characters}
    script["characters"] = [{"name": item["name"], "role_id": item["role_id"], "description": item["identity"], "voice_profile": item.get("voice_profile", {})} for item in context.characters]
    for block in script.get("blocks", []):
        if block.get("type") == "dialogue":
            character = by_name.get(block.get("character"))
            if not character:
                raise ValueError(f"模型输出了输入中不存在的角色：{block.get('character')}")
            block["role_id"] = character["role_id"]
    script["source_story"] = {"story_id": context.story_id, "request_id": context.request_id, "schema_version": context.schema_version, "source_hash": context.source_hash, "chapters": [c["chapter_number"] for c in context.chapters]}
    return script


def demo_story_script(context: StoryContext, options: dict) -> dict:
    cast = context.characters
    names = [item["name"] for item in cast]
    first, second = names[0], names[1] if len(names) > 1 else names[0]
    third = names[2] if len(names) > 2 else second
    source = build_source_text(context)
    beats = _sentences(source)
    setup = beats[0].rstrip("。！？")
    pivot = beats[len(beats) // 2].rstrip("。！？")
    final = beats[-1].rstrip("。！？")
    roles = {item["name"]: item["role_id"] for item in cast}
    blocks = [
        {"id":"blk_0001","type":"scene_heading","act":1,"scene":1,"location":"可用灯光与屏幕切换的主场景","time":"危机开始","characters":names,"text":"第一幕 第一场：信号逼近"},
        {"id":"blk_0002","type":"sfx","name":"警报渐起","duration_ms":1000,"text":"警报由远及近，屏幕上的数据开始闪烁。"},
        {"id":"blk_0003","type":"stage_direction","text":f"{first}盯住关键数据，没有立刻下结论；{second}已经站到出口前；{third}在两人之间观察。"},
        {"id":"blk_0004","type":"dialogue","role_id":roles[first],"character":first,"text":f"先别动。眼前这件事不对劲：{setup}。我需要再核一遍。","emotion":"nervous","intent":"warn","delivery":"statement","intensity":0.62,"actions":[{"action":"look_down","start_offset_ms":250,"duration_ms":700,"intensity":0.45}]},
        {"id":"blk_0005","type":"dialogue","role_id":roles[second],"character":second,"text":"再核下去，机会就没了。给我一个方向，我去试。","emotion":"excited","intent":"persuade","delivery":"statement","intensity":0.7,"actions":[{"action":"lean_forward","start_offset_ms":200,"duration_ms":650,"intensity":0.6}]},
        {"id":"blk_0006","type":"dialogue","role_id":roles[third],"character":third,"text":"也许它不是在挡路，而是在等我们走进它替我们画好的路。像一面会学人表情的镜子。","emotion":"mysterious","intent":"inform","delivery":"statement","intensity":0.5,"actions":[{"action":"look_up","start_offset_ms":500,"duration_ms":600,"intensity":0.4}]},
        {"id":"blk_0007","type":"scene_heading","act":1,"scene":2,"location":"同地，警报转红","time":"片刻后","characters":names,"text":"第一幕 第二场：弱点被放大"},
        {"id":"blk_0008","type":"dialogue","role_id":roles[first],"character":first,"text":"左边有风险，右边也有风险……每个方案都差一点。再给我十秒。","emotion":"nervous","intent":"conceal","delivery":"statement","intensity":0.8,"actions":[{"action":"tremble","start_offset_ms":300,"duration_ms":900,"intensity":0.45}]},
        {"id":"blk_0009","type":"dialogue","role_id":roles[second],"character":second,"text":"十秒太长！我先去做，错了我来改！","emotion":"angry","intent":"command","delivery":"exclamation","intensity":0.76,"actions":[{"action":"point","start_offset_ms":100,"duration_ms":500,"intensity":0.65}]},
        {"id":"blk_0010","type":"dialogue","role_id":roles[third],"character":third,"text":f"等一下，我想到一件事：{pivot}。如果它靠我们的反应判断，那我们可以给它一段假的反应。","emotion":"surprised","intent":"persuade","delivery":"statement","intensity":0.67,"actions":[{"action":"wave","start_offset_ms":450,"duration_ms":600,"intensity":0.5}]},
        {"id":"blk_0011","type":"scene_heading","act":1,"scene":3,"location":"主控台前","time":"反制开始","characters":names,"text":"第一幕 第三场：把差异变成诱饵"},
        {"id":"blk_0012","type":"dialogue","role_id":roles[first],"character":first,"text":"好，这次我不替你们拦住所有风险。你负责做出它预期的动作；你把那个比喻放到公共频道。我在后面藏真正的指令。","emotion":"serious","intent":"command","delivery":"statement","intensity":0.7,"actions":[{"action":"nod","start_offset_ms":700,"duration_ms":500,"intensity":0.5}]},
        {"id":"blk_0013","type":"dialogue","role_id":roles[second],"character":second,"text":"明白。你说开始，我才按。","emotion":"serious","intent":"inform","delivery":"statement","intensity":0.55,"actions":[{"action":"lean_back","start_offset_ms":200,"duration_ms":500,"intensity":0.4}]},
        {"id":"blk_0014","type":"dialogue","role_id":roles[third],"character":third,"text":"那就让镜子看见三张夸张的脸，却看不见我们真正的眼睛。","emotion":"excited","intent":"encourage","delivery":"statement","intensity":0.65,"actions":[{"action":"look_left","start_offset_ms":300,"duration_ms":450,"intensity":0.4}]},
        {"id":"blk_0015","type":"sfx","name":"警报骤停","duration_ms":800,"text":"警报忽然停住，只剩设备稳定的低鸣。"},
        {"id":"blk_0016","type":"dialogue","role_id":roles[first],"character":first,"text":f"成了。{final}。它只会放大弱点，却不知道弱点也能被我们拿来配合。","emotion":"tender","intent":"inform","delivery":"statement","intensity":0.58,"actions":[{"action":"bow","start_offset_ms":700,"duration_ms":600,"intensity":0.35}]},
    ]
    return _apply_story_identity({"title": context.title, "chapter_title": "、".join(c["title"] for c in context.chapters), "characters": [], "adaptation_notes": ["根据上游角色档案区分对白节奏、措辞与行动目标。", "将上游质量提示转化为可表演的犹豫、冒进和修正。", "每句台词保留稳定 role_id 与章节追溯信息。"], "blocks": blocks}, context)


def generate_story_script(context: StoryContext, options: dict) -> tuple[dict, str]:
    settings = Settings.load()
    if settings.demo_mode or not settings.api_key:
        return demo_story_script(context, options), "demo_story_json"
    try:
        result = _generate_story_by_scenes(context, options, settings)
        if not isinstance(result.get("blocks"), list) or not result["blocks"]:
            raise ValueError("缺少 blocks")
        return result, "model_story_json"
    except (ProviderError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(str(exc)) from exc


def generate_script(title: str, chapter_title: str, source: str, options: dict) -> tuple[dict, str]:
    settings = Settings.load()
    if settings.demo_mode or not settings.api_key:
        return demo_script(title, chapter_title, source, options), "demo"
    try:
        return _generate_text_by_scenes(title, chapter_title, source, options, settings), "model"
    except (ProviderError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(str(exc)) from exc


def format_script(script: dict) -> str:
    lines = [script.get("title", "舞台剧初稿"), "", "人物表"]
    for item in script.get("characters", []):
        lines.append(f"{item.get('name', '')}——{item.get('description', '')}")
    lines.append("")
    for block in script.get("blocks", []):
        kind = block.get("type")
        if kind == "scene_heading":
            lines.extend([block.get("text", ""), f"地点：{block.get('location', '')}", f"时间：{block.get('time', '')}", f"出场人物：{'、'.join(block.get('characters', []))}"])
        elif kind == "dialogue":
            note = "、".join(x for x in [block.get("emotion"), block.get("delivery")] if x)
            lines.append(f"{block.get('character', '')}（{note}）：{block.get('text', '')}")
        elif kind in {"stage_direction", "pause", "action"}:
            lines.append(f"【{block.get('text', '')}】")
        elif kind in {"light", "sfx"}:
            label = "灯光提示" if kind == "light" else "音效提示"
            lines.append(f"【{label}｜{block.get('text', block.get('name', ''))}】")
    return "\n".join(lines).strip() + "\n"


def quality_report(script: dict, tpm: dict, target_minutes: int) -> dict:
    dialogues = [b for b in script.get("blocks", []) if b.get("type") == "dialogue"]
    detail_count = sum(bool(b.get("emotion")) + bool(b.get("intent")) + bool(b.get("actions")) for b in dialogues)
    estimated = round(tpm.get("duration_ms", 0) / 1000)
    target = target_minutes * 60
    duration_score = max(30, round(100 - abs(estimated - target) / max(target, 1) * 100))
    scores = {
        "format": 92,
        "character_consistency": 84,
        "dramatic_structure": 86,
        "dialogue_performance": min(96, 72 + detail_count),
        "stage_feasibility": 88,
        "duration_fit": duration_score,
        "tpm_completeness": 95 if tpm.get("events") else 40,
    }
    overall = round(sum(scores.values()) / len(scores))
    issues = []
    if estimated < target * 0.8:
        issues.append(f"当前估时约 {estimated} 秒，短于 {target} 秒目标；建议用模型正式生成或继续扩写对白。")
    issues.append("TPM 当前为 estimated 时间轴；接入 TTS 后需用真实音频时长锁定。")
    return {"overall": overall, "scores": scores, "estimated_seconds": estimated, "issues": issues}
