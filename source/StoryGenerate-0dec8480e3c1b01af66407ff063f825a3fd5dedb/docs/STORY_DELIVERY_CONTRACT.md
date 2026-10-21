# StoryDelivery 数据交付协议 v1.0

> 适用于 StoryGenerate 创作引擎向音频演绎、播放控制、玩具调度等下游系统交付结构化故事数据。
> 最后更新: 2026-10-12 | 格式版本: `delivery_format_version = "1.0"`

---

## 1. 概述

StoryDelivery 是 StoryGenerate 创作引擎的**标准化输出格式**，是一份同时包含：

- 完整可读故事台本
- 结构化场景、角色、台词数据
- 演绎提示（情绪、语气、语速、重读、停顿）
- 版本追溯信息

的 JSON 文档。下游团队无需重新拆分整篇故事即可直接消费结构化数据。

**设计原则:**
- **自描述**: 字段命名清晰，层级明确，无需额外 Schema 解释即可理解。
- **可追溯**: 每份交付都关联到已批准的 Plan、故事版本和创作参数。
- **可验证**: 结构完整性可通过确定性校验自动检查，不依赖 LLM 判断。
- **可演进而兼容**: 通过 `delivery_format_version` 标识格式变化，新字段向后兼容。

---

## 2. 数据层级

```
StoryDelivery                          # 顶层：故事交付
├── character_snapshot[]               # 角色快照（输入角色）
├── character_portrayals[]             # 角色画像（Plan 生成的角色信息）
├── outline                             # 已批准的故事大纲
├── episodes[]                         # 剧集列表
│   ├── scenes[]                       # 场景列表
│   │   ├── utterances[]               # 台词/音效/动作列表
│   │   │   ├── emphasis[]             # 重读区间
│   │   │   └── pauses[]               # 停顿标记
│   │   └── ...
│   └── ...
├── continuity_state                   # 跨集连续性状态
├── quality_report                     # 自动质量校验报告
└── model_trace[]                      # 模型调用记录
```

---

## 3. 字段说明

### 3.1 StoryDelivery（顶层）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `story_id` | string | ✅ | 故事唯一标识 |
| `story_version_id` | string | ✅ | 故事版本标识 |
| `version_no` | int | ✅ | 版本号，从 1 开始 |
| `parent_story_version_id` | string\|null | ✅ | 续写/修订时的父版本 |
| `lineage_type` | enum | ✅ | "create" \| "continue" \| "revise" \| "remix" |
| `series_id` | string\|null | | 系列标识（连载故事使用） |
| `approved_plan_id` | string | ✅ | 溯源到已批准 Plan 的 ID |
| `approved_plan_revision` | int | ✅ | 溯源到已批准 Plan 的修订版本 |
| `title` | string | ✅ | 故事标题 |
| `summary` | string | ✅ | 故事概要（premise） |
| `preview_blurb` | string | ✅ | 预告文案，15-160 字，单段 |
| `tags` | string[] | | 标签（体裁、风格、受众、分级） |
| `story_mode` | string | ✅ | 故事模式（general / viral_drama / mystery / serialized） |
| `content_form` | string | ✅ | 固定为 "audio_drama" |
| `character_snapshot` | CharacterSnapshot[] | ✅ | 角色快照列表 |
| `character_portrayals` | dict[] | ✅ | Plan 生成的角色画像 |
| `outline` | PlanContent | ✅ | 已批准的故事大纲 |
| `episodes` | EpisodeDelivery[] | ✅ | 剧集交付列表 |
| `continuity_state` | dict | ✅ | 跨集连续性（facts, relationships, unresolved_threads） |
| `quality_report` | PerformanceValidationReport | ✅ | 自动质量校验报告 |
| `ready_for_playback` | bool | ✅ | 是否通过校验，可播放 |
| `status` | enum | | "CANDIDATE" \| "READY" \| "SUPERSEDED" \| "ARCHIVED" |
| `engine_version` | string | | 创作引擎版本 |
| `delivery_format_version` | string | ✅ | 交付格式版本，当前为 "1.0" |
| `rule_versions` | dict | | 规则库版本信息 |
| `model_trace` | dict[] | | 模型调用记录（仅供调试） |
| `created_at` | datetime | | 创建时间 |

### 3.2 CharacterSnapshot（角色快照）

输入角色的原始信息。

| 字段 | 类型 | 说明 |
|---|---|---|
| `character_id` | string | 人物唯一标识 |
| `role_id` | string\|null | 配音角色 ID |
| `performer_id` | string\|null | 演绎者 ID |
| `canon` | dict | 人物基本信息（name, immutable_facts 等） |
| `profile` | dict | 人物深层信息（goal, motivation, fear, speech_style 等） |
| `canon_revision` | int | 基本信息版本 |
| `profile_revision` | int | 人物资料版本 |

### 3.3 CharacterPortrayal（角色画像）

Plan 阶段生成的角色创作指南，存储为 dict：

| 字段 | 类型 | 说明 |
|---|---|---|
| `role_id` | string | 角色 ID（与 CharacterSnapshot.role_id 对应） |
| `story_name` | string | 剧中称呼名 |
| `external_goal` | string | 外部目标 |
| `emotional_avoidance` | string | 情感回避 |
| `speech_style` | string | 说话风格 |
| `relationship_stance` | string | 关系立场 |

### 3.4 EpisodeDelivery（剧集）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `episode_id` | string | ✅ | 剧集唯一标识 |
| `index` | int | ✅ | 剧集序号，从 1 开始 |
| `title` | string | ✅ | 剧集标题 |
| `synopsis` | string | | 剧集梗概 |
| `duration_target_sec` | int | ✅ | **目标**时长（秒），用户设定值 |
| `duration_estimate_sec` | int | ✅ | **预估**时长（秒），基于发声文本字符数 ÷ 3.5 字符/秒的估算 |
| `scenes` | SceneDelivery[] | ✅ | 场景列表 |
| `episode_summary` | string | | 剧集摘要 |
| `ending_hook` | string | | 结尾悬念 |
| `fact_delta` | dict[] | | 本集新增/修改的事实 |
| `relationship_delta` | dict[] | | 本集关系变化 |
| `new_open_threads` | string[] | | 新开启的剧情线 |
| `closed_threads` | string[] | | 已关闭的剧情线 |
| `validation_status` | enum | | "PENDING" \| "PASSED" \| "FAILED" |
| `ready_for_playback` | bool | | 本集是否可播放 |

### 3.5 SceneDelivery（场景）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `scene_id` | string | ✅ | 场景唯一标识，格式 `ep{episode}-scene{index}` |
| `scene_index` | int | ✅ | 场景在剧集内的序号，从 1 开始 |
| `title` | string | ✅ | 场景标题 |
| `location` | string | | 场景地点 |
| `dramatic_goal` | string | | 本场景的戏剧目标 |
| `utterances` | Utterance[] | ✅ | 台词/音效/动作列表 |

### 3.6 Utterance（台词/音效/动作）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `line_id` | string | ✅ | 全局唯一行标识，格式 `ep{ep}-s{scene}-l{line}` |
| `kind` | enum | ✅ | "dialogue" \| "narration" \| "sfx" \| "music" \| "action" |
| `speaker_role_id` | string\|null | | dialogue 类型必填：说话角色的 role_id |
| `narrator_id` | string\|null | | narration 类型必填：旁白者 ID |
| `performer_id` | string\|null | | 指定演绎者 ID |
| `text` | string | ✅ | **实际台词文本**或音效/动作描述 |
| `emotion` | Emotion\|null | | 情绪标注 |
| `tone_instruction` | string\|null | | 语气指导（自然语言） |
| `emphasis` | EmphasisSpan[] | | 重读区间列表 |
| `speech_rate` | float | | 语速倍率，默认 1.0，范围 [0.5, 2.0] |
| `pitch` | string\|null | | 音高指导 |
| `volume` | string\|null | | 音量指导 |
| `pauses` | PauseCue[] | | 停顿标记 |
| `delivery_note` | string\|null | | 综合演绎提示 |
| `audio_cue_ref` | string\|null | | 音效/音乐引用 |

**`kind` 使用规则:**
- `dialogue`: 人物对白。`speaker_role_id` 必填，`text` 是真实要说的话。
- `narration`: 旁白。`narrator_id` 必填。
- `sfx`: 音效描述。无 speaker，`text` 是音效说明。
- `music`: 音乐提示。无 speaker，`text` 是音乐说明。
- `action`: 舞台动作。无 speaker，`text` 是动作描述。

**重要: 括号中的动作说明（如"(冷笑)"）应作为独立的 `action` 或标注在 `tone_instruction` 中，不得混入 dialogue 的 `text`。**

### 3.7 Emotion（情绪）

枚举值：`neutral`, `calm`, `happy`, `excited`, `sad`, `worried`, `angry`, `surprised`, `fearful`, `reassuring`, `serious`, `playful`, `mysterious`, `tender`

### 3.8 EmphasisSpan（重读区间）

| 字段 | 类型 | 说明 |
|---|---|---|
| `span_text` | string | 要重读的文字 |
| `occurrence` | int | 第几次出现（≥1） |
| `start_char` | int\|null | 解析后的起始字符位置 |
| `end_char` | int\|null | 解析后的结束字符位置 |
| `strength` | enum | "medium" \| "strong" |

### 3.9 PauseCue（停顿标记）

| 字段 | 类型 | 说明 |
|---|---|---|
| `after_char` | int | 在第几个字符之后停顿（≥0） |
| `duration_ms` | int | 停顿时长，毫秒（0-5000） |

---

## 4. 角色 ID 规则

| 角色来源 | role_id 格式 | 说明 |
|---|---|---|
| 用户预设角色 | 用户指定（如 "protagonist"） | 在 character_snapshot 中定义 |
| Plan 生成角色 | 模型自由命名（如 "husband"） | 在 character_portrayals 中定义 |
| 旁白 | "narrator" | 系统内置，始终可用 |

**下游使用 `build_character_role_map()` 获取完整的 `{role_id → 角色详情}` 映射。**

---

## 5. 校验保证

StoryGenerate 引擎在交付前自动执行：

1. **台词 ID 唯一性**: 所有 `line_id` 全局不重复
2. **角色存在性**: 所有 `speaker_role_id` 都能在 `character_snapshot` 或 `character_portrayals` 中找到
3. **台词类型分离**: `dialogue` 行不会以 `(` 开头（防止动作说明混入对白）
4. **情绪/停顿/重读合规**: 符合 Schema 约束
5. **时长一致性**: 预估时长基于文本字数，不伪装为实际 TTS 时长
6. **JSON 往返一致性**: 序列化→反序列化→关键字段不变

**不保证的内容（需下游自行处理）:**
- 实际 TTS 音频时长（仅提供字符数估算）
- 音频文件存在性
- 演绎者的实际音色匹配
- 多角色同步调度

---

## 6. 下游消费示例

### 6.1 获取全部台词（用于 TTS 合成）

```python
from drama_engine.workflow.delivery import delivery_from_json

delivery = delivery_from_json(open("story.json").read())
for ep in delivery.episodes:
    for scene in ep.scenes:
        for u in scene.utterances:
            if u.kind in ("dialogue", "narration"):
                print(f"[{u.speaker_role_id or u.narrator_id}] {u.text}")
```

### 6.2 获取角色映射（用于玩具分配）

```python
from drama_engine.workflow.delivery import build_character_role_map

role_map = build_character_role_map(delivery)
for role_id, info in role_map.items():
    print(f"{role_id}: {info['story_name']} → performer={info['performer_id']}")
```

### 6.3 获取可读台本

```python
from drama_engine.workflow.delivery import delivery_to_readable_script

print(delivery_to_readable_script(delivery))
```

### 6.4 批量导出

```python
from drama_engine.workflow.delivery import export_delivery_bundle

files = export_delivery_bundle(delivery, "output/", "my_story")
# → output/my_story.json          (结构化 JSON)
# → output/my_story_script.txt    (可读台本)
# → output/my_story_characters.json (角色映射)
```

---

## 7. 格式版本兼容规则

| 版本 | 变更 |
|---|---|
| `"1.0"` | 初始版本。包含 character_portrayals, scene_index, delivery_format_version。 |

当 `delivery_format_version` 递增时：
- 新增字段设置合理默认值，供新客户端读取旧 JSON。消费端须忽略未知字段；使用旧版严格 Schema 的客户端需升级后才能读取新增字段。
- 删除字段前至少保留一个主版本过渡期。
- 语义变更（如字段单位或枚举值变化）在新主版本中完成。

---

## 8. JSON 示例

参见真实生成的 StoryDelivery JSON 产物（`6-实测产物/acceptance/` 目录中的 JSON 文件），或运行：

```python
from drama_engine.workflow.delivery import delivery_to_json
print(delivery_to_json(delivery))
```
