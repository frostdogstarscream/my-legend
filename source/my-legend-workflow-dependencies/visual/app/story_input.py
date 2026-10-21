"""story.v3 输入归一化：把上游故事数据变成安全、可追溯的改编上下文。"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


class StoryInputError(ValueError):
    pass


@dataclass(frozen=True)
class StoryContext:
    story_id: str
    request_id: str
    schema_version: str
    title: str
    genre: str
    tone: str
    theme: str
    logline: str
    characters: list[dict]
    relationships: list[dict]
    chapters: list[dict]
    warnings: list[str]
    source_hash: str

    def public_preview(self) -> dict:
        return {
            "story_id": self.story_id,
            "request_id": self.request_id,
            "schema_version": self.schema_version,
            "title": self.title,
            "genre": self.genre,
            "tone": self.tone,
            "characters": [{"role_id": c["role_id"], "name": c["name"], "identity": c.get("identity", ""), "dialogue_style": c.get("dialogue_style", {})} for c in self.characters],
            "chapters": [{"chapter_number": c["chapter_number"], "title": c["title"], "summary": c.get("summary", ""), "character_count": len(c.get("participating_role_ids", [])), "text_length": len(c["content"])} for c in self.chapters],
            "warnings": self.warnings,
            "source_hash": self.source_hash,
        }


def _as_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StoryInputError(f"{field} 必须是非空文本")
    return value.strip()


def _parse_tpm_document(document: dict[str, Any], selected_chapters: list[int] | None = None) -> StoryContext:
    """把玩具表演协议 TPM 还原为可继续改编的单章故事上下文。"""
    if selected_chapters and 1 not in selected_chapters:
        raise StoryInputError("TPM 导入仅包含第 1 章；请勾选该章节")
    title = _as_string(document.get("title"), "TPM.title")
    raw_actors = document.get("actors")
    events = document.get("events")
    if not isinstance(raw_actors, list) or not raw_actors:
        raise StoryInputError("TPM.actors 必须至少包含一名角色")
    if not isinstance(events, list) or not events:
        raise StoryInputError("TPM.events 必须至少包含一个表演事件")
    characters: list[dict] = []
    actor_names: dict[str, str] = {}
    for index, actor in enumerate(raw_actors, 1):
        if not isinstance(actor, dict):
            raise StoryInputError(f"TPM.actors[{index}] 必须是对象")
        role_id = _as_string(actor.get("actor_id"), f"TPM.actors[{index}].actor_id")
        name = _as_string(actor.get("character_name"), f"TPM.actors[{index}].character_name")
        actor_names[role_id] = name
        characters.append({"role_id": role_id, "name": name, "identity": "从 TPM 表演协议导入的角色", "personality_traits": [], "strengths": [], "weaknesses": [], "goals": [], "constraints": [], "motivation": "", "fear_or_concern": "", "behavior_style": {}, "dialogue_style": {}, "locked_fields": []})
    lines: list[str] = []
    participant_ids: list[str] = []
    for event in sorted((event for event in events if isinstance(event, dict)), key=lambda item: int(item.get("start_ms", 0))):
        kind = event.get("type")
        if kind == "speech":
            actor_id = event.get("actor_id")
            if actor_id not in actor_names:
                raise StoryInputError(f"TPM 事件引用了不存在的 actor_id：{actor_id}")
            participant_ids.append(actor_id)
            performance = event.get("performance") if isinstance(event.get("performance"), dict) else {}
            emotion = performance.get("emotion", {}).get("primary", "neutral") if isinstance(performance.get("emotion"), dict) else "neutral"
            lines.append(f"{actor_names[actor_id]}（{emotion}）：{_as_string(event.get('text'), 'TPM speech.text')}")
        elif kind in {"sfx", "light", "action"}:
            label = {"sfx": "音效", "light": "灯光", "action": "动作"}[kind]
            lines.append(f"【{label}：{str(event.get('name') or event.get('text') or '').strip()}】")
    if not lines:
        raise StoryInputError("TPM 中没有可转化的台词或舞台事件")
    payload = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return StoryContext(
        story_id=str(document.get("show_id") or "tpm_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]),
        request_id="", schema_version=f"tpm/{document.get('version', 'unknown')}",
        title=title, genre="", tone="", theme="", logline="从 TPM 表演时间轴还原，供扩写为完整舞台剧。",
        characters=characters, relationships=[],
        chapters=[{"chapter_number": 1, "title": "TPM 导入演出稿", "summary": "按原时间顺序保留的台词、音效与动作提示。", "content": "\n".join(lines), "participating_role_ids": list(dict.fromkeys(participant_ids))}],
        warnings=["已从 TPM 表演协议还原台词与舞台提示；原始章节、人物关系和未写入 TPM 的剧情信息无法恢复。"],
        source_hash=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    )


def parse_story_document(document: dict[str, Any], selected_chapters: list[int] | None = None) -> StoryContext:
    if not isinstance(document, dict):
        raise StoryInputError("JSON 顶层必须是对象")
    if document.get("schema") == "tpm":
        return _parse_tpm_document(document, selected_chapters)
    # 兼容 API 常见的结果包装，可能多层嵌套：
    # {data:{...}} / {result:{...}} 以及 Dify 工作流响应 {data:{outputs:{result:{...}}}}。
    for _ in range(5):
        if isinstance(document.get("story"), dict) or isinstance(document.get("chapters"), list):
            break
        layer_error = document.get("error")
        if isinstance(layer_error, str) and layer_error.strip():
            raise StoryInputError(f"上游工作流执行失败：{layer_error.strip()}")
        nested = next(
            (document[key] for key in ("data", "result", "output", "outputs", "payload") if isinstance(document.get(key), dict)),
            None,
        )
        if nested is None:
            break
        document = nested
    story = document.get("story")
    input_normalized = False
    if not isinstance(story, dict):
        # 兼容常见扁平格式：title / chapters / characters 直接位于顶层。
        # 这类数据在导入时归一化为 story.v3，用户无需手工添加 story 包装层。
        flat_chapters = document.get("chapters")
        if isinstance(flat_chapters, list) and flat_chapters:
            story = {
                "title": document.get("title") or document.get("name") or "未命名故事",
                "genre": document.get("genre", ""), "tone": document.get("tone", ""),
                "theme": document.get("theme", ""), "logline": document.get("logline", ""),
                "chapters": flat_chapters,
            }
            input_normalized = True
        else:
            raise StoryInputError("未识别到故事章节：请提供 story.chapters，或在顶层提供 chapters 数组")
    raw_story_id = document.get("story_id") or document.get("id")
    story_id = str(raw_story_id).strip() if raw_story_id else "import_" + hashlib.sha256(
        json.dumps(document, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    request_id = str(document.get("request_id", ""))
    meta = document.get("meta") if isinstance(document.get("meta"), dict) else {}
    schema_version = str(meta.get("schema_version", "unknown"))
    raw_characters = document.get("characters", document.get("roles"))
    if not isinstance(raw_characters, list) or not raw_characters:
        raise StoryInputError("characters 必须至少包含一名角色")
    role_ids: set[str] = set()
    characters: list[dict] = []
    for index, raw in enumerate(raw_characters, 1):
        if not isinstance(raw, dict):
            raise StoryInputError(f"characters[{index}] 必须是对象")
        role_id = str(raw.get("role_id") or raw.get("id") or f"role_{index:02d}").strip()
        if role_id in role_ids:
            raise StoryInputError(f"role_id 重复：{role_id}")
        role_ids.add(role_id)
        characters.append({
            "role_id": role_id, "name": _as_string(raw.get("name"), f"characters[{index}].name"),
            "identity": str(raw.get("identity", "")), "personality_traits": raw.get("personality_traits", []),
            "strengths": raw.get("strengths", []), "weaknesses": raw.get("weaknesses", []),
            "goals": raw.get("goals", []), "constraints": raw.get("constraints", []),
            "motivation": str(raw.get("motivation", "")), "fear_or_concern": str(raw.get("fear_or_concern", "")),
            "behavior_style": raw.get("behavior_style", {}) if isinstance(raw.get("behavior_style"), dict) else {},
            "dialogue_style": raw.get("dialogue_style", {}) if isinstance(raw.get("dialogue_style"), dict) else {},
            "voice_profile": raw.get("voice_profile", {}) if isinstance(raw.get("voice_profile"), dict) else {},
            "locked_fields": raw.get("locked_fields", []) if isinstance(raw.get("locked_fields"), list) else [],
        })
    raw_chapters = story.get("chapters")
    if not isinstance(raw_chapters, list) or not raw_chapters:
        raise StoryInputError("story.chapters 必须至少包含一章")
    seen_numbers: set[int] = set()
    chapters: list[dict] = []
    wanted = set(selected_chapters or [])
    for raw in raw_chapters:
        if not isinstance(raw, dict):
            raise StoryInputError("每章必须是对象")
        number = raw.get("chapter_number", raw.get("number", raw.get("index")))
        if not isinstance(number, int):
            raise StoryInputError("每章必须包含整数 chapter_number（扁平格式可使用 number 或 index）")
        if number in seen_numbers:
            raise StoryInputError(f"chapter_number 重复：{number}")
        seen_numbers.add(number)
        if wanted and number not in wanted:
            continue
        participant_ids = raw.get("participating_role_ids", raw.get("role_ids", []))
        if not isinstance(participant_ids, list) or any(role_id not in role_ids for role_id in participant_ids):
            raise StoryInputError(f"第 {number} 章引用了不存在的角色")
        chapters.append({"chapter_number": number, "title": _as_string(raw.get("title", raw.get("name")), f"第 {number} 章 title"), "summary": str(raw.get("summary", "")), "content": _as_string(raw.get("content", raw.get("text")), f"第 {number} 章 content"), "participating_role_ids": participant_ids})
    if wanted and wanted - seen_numbers:
        raise StoryInputError("所选章节不存在")
    if not chapters:
        raise StoryInputError("请至少选择一章")
    relationships = document.get("relationships", [])
    if not isinstance(relationships, list):
        raise StoryInputError("relationships 必须是数组")
    normalized_relationships = []
    for rel in relationships:
        if not isinstance(rel, dict):
            continue
        if rel.get("role_a") not in role_ids or rel.get("role_b") not in role_ids:
            raise StoryInputError("关系引用了不存在的角色")
        normalized_relationships.append(rel)
    warnings = [str(w) for w in document.get("warnings", []) if str(w).strip()]
    if input_normalized:
        warnings.insert(0, "已将扁平 JSON 自动归一化为 story.v3 输入格式。")
    if document.get("status") != "success":
        warnings.insert(0, f"上游状态：{document.get('status', 'unknown')}")
    if meta.get("degraded_level", 0):
        warnings.append(f"上游降级等级：{meta['degraded_level']}；请人工复核人物与情节。")
    # story.v3.1 使用 quality_result，早期样例使用 quality_report，两者结构一致。
    quality = document.get("quality_result") or document.get("quality_report")
    if isinstance(quality, dict):
        if quality.get("available") and quality.get("passed") is False:
            warnings.append(f"上游质量校验未通过（总分 {quality.get('overall_score', '未知')}），请人工复核后再改编。")
        issues = quality.get("issues")
        if isinstance(issues, list):
            for issue in issues:
                if isinstance(issue, dict) and issue.get("description"):
                    warnings.append(f"上游质量提示：{issue['description']}")
    errors = document.get("errors")
    if isinstance(errors, list):
        for error in errors:
            if str(error).strip():
                warnings.append(f"上游错误：{error}")
    payload = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return StoryContext(
        story_id=story_id, request_id=request_id, schema_version=schema_version,
        title=_as_string(story.get("title"), "story.title"), genre=str(story.get("genre", "")),
        tone=str(story.get("tone", "")), theme=str(story.get("theme", "")), logline=str(story.get("logline", "")),
        characters=characters, relationships=normalized_relationships, chapters=sorted(chapters, key=lambda x: x["chapter_number"]),
        warnings=list(dict.fromkeys(warnings)), source_hash=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    )


def build_source_text(context: StoryContext) -> str:
    return "\n\n".join(f"第{chapter['chapter_number']}章 {chapter['title']}\n{chapter['content']}" for chapter in context.chapters)
