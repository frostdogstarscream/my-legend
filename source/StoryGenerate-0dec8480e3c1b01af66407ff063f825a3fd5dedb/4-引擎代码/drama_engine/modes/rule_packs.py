"""Phase 9: Mode-specific Rule Packs — data, not code.

Future: load from JSON/YAML into RuleRepository.
"""
MYSTERY_RULES: list[dict] = [
    {"rule_id": "mystery_clue_01", "text": "线索必须唯一标识，每条线索有明确来源和引入节点。"},
    {"rule_id": "mystery_clue_02", "text": "揭示必须基于此前已出现的至少一条线索。"},
    {"rule_id": "mystery_fair_01", "text": "封闭式谜题：最终解答必须能用已出现的线索支撑，禁止最后一刻凭空出现关键事实。"},
    {"rule_id": "mystery_fair_02", "text": "开放式谜题：允许 unresolved central question，但必须有合理的 clue progression。"},
    {"rule_id": "mystery_red_herring_01", "text": "红鲱鱼（误导线索）允许存在，但默认不被接受为证据，除非结构数据标记它被重新解释。"},
]

SERIALIZED_RULES: list[dict] = [
    {"rule_id": "serialized_01", "text": "每集必须有明确的 episode goal。"},
    {"rule_id": "serialized_02", "text": "每集必须有实际事件推进，不能原地踏步。"},
    {"rule_id": "serialized_03", "text": "open_threads 和 resolved_threads 状态必须一致。"},
    {"rule_id": "serialized_04", "text": "ending_preference=cliffhanger 时必须有 continuation_hook。"},
    {"rule_id": "serialized_05", "text": "ending_preference=closed 时不强制 cliffhanger。"},
]

FORM_RULES: list[dict] = [
    {"rule_id": "novel_01", "text": "Novel: 叙事性正文，章节/场景组织，对白嵌入叙述。"},
    {"rule_id": "storytelling_01", "text": "Storytelling: 口语化、可讲述、讲述者主导。"},
    {"rule_id": "standup_01", "text": "Standup: 单人表演，setup/punchline/callback 结构。"},
    {"rule_id": "crosstalk_01", "text": "Crosstalk: 双人对话，逗哏/捧哏 exchange 递进。"},
]