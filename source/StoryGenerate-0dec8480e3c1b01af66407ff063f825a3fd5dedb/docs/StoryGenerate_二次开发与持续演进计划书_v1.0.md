# StoryGenerate 二次开发与持续演进计划书
## —— 从“广播剧/爆款短剧生成引擎”演进为模块化 AI Story Engine

**文档版本：v1.0**  
**适用项目：** `https://github.com/zblds-a/StoryGenerate`  
**目标读者：** Codex / Claude Code / Cursor / 开发人员 / 测试人员 / 产品与需求负责人  
**基线版本：** StoryGenerate 0.2.1（以当前 main 分支为基线）  
**核心原则：** 不推翻已有有效能力，先保证可用、可控、好看，再逐步抽象、数据库化、扩展 Mode 与内容形态。

---

# 0. 结论摘要

本项目建议**继续基于当前 StoryGenerate + LangGraph 框架演进，逐步退出 Dify 故事生成工作流**。

当前项目已经具备较好的工程基础：

- LangGraph 主图、条件边、修复回路、Checkpoint；
- Pydantic Schema-First；
- LLM Provider / OpenAI Compatible 模型适配；
- 模型分级路由；
- JSON 规则库；
- 人物行为卡、事实账本、结构校验、语义校验；
- Mock 与回归矩阵；
- Token / 调用次数 / Trace 等可观测能力。

因此不建议重新开发一套故事引擎，而应将现有项目重构为：

```text
Story Engine Core
│
├── Mode System
│   ├── viral_drama
│   ├── general
│   ├── mystery
│   ├── serialized
│   └── future...
│
├── Content Form System
│   ├── audio_drama
│   ├── prose_story
│   ├── novel
│   ├── storytelling
│   ├── standup
│   ├── crosstalk
│   └── future...
│
├── Template / Rule Pack
├── Character System
├── Memory / History
├── LLM Runtime
├── Quality / Validator
└── Persistence / Database
```

其中：

- **Mode 决定“故事怎样构思和推进”**；
- **Content Form 决定“最终写成什么形态”**；
- **Template 决定“具体采用什么叙事结构”**；
- **Rule Pack 决定“必须遵守哪些质量规则”**；
- **Character Template 决定“角色是谁”**；
- **Runtime Character State 决定“角色在这一次故事里怎样表现”**。

新增普通故事模板时，原则上**只新增数据库配置，不新增一条代码工作流**；只有生成机制确实发生变化时，才新增 Mode 或 Capability。

---

# 1. 本次二开核心目标

## 1.1 第一目标：故事必须“好看、想继续听/看”

故事质量以“吸引力”为第一业务目标。

当前项目中短剧、网文、爽文常用的创作机制不应因为“套路化”而被排斥。相反，应把其中已经被市场验证的有效机制抽象成可复用能力，例如：

- 强开场 Hook；
- 明确欲望与目标；
- 快速建立冲突；
- 信息差；
- 金手指 / 特殊能力；
- 能力边界与代价；
- 冲突升级；
- 阶段性收益；
- 误判与反转；
- 角色在压力下做选择；
- 选择必须付出代价；
- 关系变化必须有事件支撑；
- 伏笔与回收；
- 悬念；
- Cliffhanger；
- 事实与资源连续性。

**不以“文学性高低”作为唯一评价标准。**

第一阶段重点仍应保留并吸收当前项目的 Viral Drama 机制，而不是将其删除。

---

## 1.2 第二目标：生成时间必须可控，失败必须明确

禁止出现：

```text
用户提交请求
→ 等待很久
→ 最后输出为空
```

系统必须满足：

```text
能生成：
→ 在明确的执行预算内成功返回

不能生成：
→ 明确返回 failed + error_code + 用户可理解的错误信息
```

不能用空字符串、空 JSON、Mock 文本或无关故事冒充成功。

生产环境必须遵循：

> **Fail Explicitly，而不是 Silent Fallback。**

---

## 1.3 第三目标：模块化，可持续二开

未来必须能够比较低成本地增加：

- 新 Story Mode；
- 新故事模板；
- 新规则包；
- 新 Content Form；
- 新模型；
- 新 Writer；
- 新 Validator；
- 新的长篇生成策略；
- 新记忆策略；
- 新角色模板；
- 新世界观；
- 新的互动剧情能力。

不能再回到“复制一条巨大工作流再修改”的方式。

---

## 1.4 第四目标：数据库化

脱离 Dify 后，应正式引入持久化能力，用于：

- 故事生成历史；
- 生成任务状态；
- 角色模板库；
- 故事模板库；
- Rule Pack 元信息；
- 故事成品；
- 角色长期记忆；
- 故事运行态；
- 模型调用日志；
- 质量评估结果；
- 后续世界观库。

---

## 1.5 第五目标：角色“半成品”机制必须长期保留

领导强调的设计原则需要固化为系统规则：

> **角色模板只固定“角色是谁”，不要把“每次故事里他必须怎样表现”全部写死。**

角色应分为：

```text
Character Canon
固定身份、姓名、性别、年龄段、物种、外形、固有能力、固定事实
        ↓
Runtime Character State
人格倾向、目标、动机、恐惧、立场、关系、选择规律、成长弧
        ↓
本次故事
```

其中 Runtime Character State：

- 用户可指定；
- 模板可提供默认值；
- 未指定时由 Story Director 演绎。

---

# 2. 当前 StoryGenerate 基线分析

以下分析基于当前仓库 main 分支中的：

```text
README.md
2-工作流契约/workflow-spec.json
4-引擎代码/drama_engine/graph.py
4-引擎代码/drama_engine/state.py
4-引擎代码/drama_engine/schemas.py
4-引擎代码/drama_engine/contracts.py
4-引擎代码/drama_engine/config.py
4-引擎代码/drama_engine/prompts.py
4-引擎代码/drama_engine/llm/
5-回归验证/
本轮修改说明.md
```

## 2.1 应保留的现有设计

### A. LangGraph 状态机

当前已经具备：

```text
串行立项
→ Gate
→ Repair
→ 再 Gate
→ fan-out
→ 汇总
```

不应推翻。

### B. Schema-First

当前 Pydantic 强类型设计是后续工程化的重要基础。

必须继续坚持：

```text
LLM 输出
→ Pydantic Parse
→ Validator
→ 业务 State
```

禁止重新退化为“到处传任意 dict + 字符串 JSON”。

### C. Rule Library

当前：

```text
规则存 JSON
代码只负责执行
```

方向正确。

应继续升级为：

```text
RuleRepository
```

使规则来源可以是：

```text
文件
数据库
缓存
```

但 Node 不关心存储位置。

### D. 模型分级路由

当前已经区分：

```text
reasoning
strong
cheap
```

这是控制延迟、成本和质量的重要基础，继续保留。

### E. Behavior Card

当前：

```text
压力 × 选择 + 代价
```

比简单的“勇敢、善良、聪明”更有利于产生真实戏剧行为。

建议保留为 Runtime Character 的高级动态字段。

### F. Fact Ledger

长篇、多集、互动故事中应继续使用。

它是倒计时、资源、认知、承诺、持有物、身份/处境的单一事实来源。

### G. Validators + Regression

现有 Tier-1、Tier-2、Evidence Judge 以及回归矩阵值得保留。

未来每次修改 Prompt / Rule / Model 都必须能够重新回归。

---

# 3. 当前必须优先修正的问题

## 3.1 生产环境不能自动回落 Mock

当前模型适配器存在：

```text
未配置 API Key
→ 自动使用 MockLLMProvider
```

测试环境可以，**生产环境禁止**。

否则可能发生：

```text
真实模型配置失败
→ 系统仍返回一篇固定 Mock 故事
→ API 看起来 success
```

这是严重线上风险。

### 改造要求

增加：

```text
APP_ENV=dev|test|prod
```

规则：

```text
dev/test:
允许 Mock

prod:
没有真实 Provider
→ 应用启动失败
```

---

## 3.2 当前单次模型超时和重试过大

当前 OpenAI Compatible Provider 默认：

```text
timeout_sec = 120
max_retries = 2
```

意味着一个节点最坏可能经历多次长等待。

同时多个 Repair Node 又允许多轮修复。

必须改为：

```text
Request Deadline
    ↓
Node Timeout
    ↓
Retry Budget
    ↓
Repair Budget
```

四层预算。

---

## 3.3 Repair 不能无限消耗时间

当前一些 Gate 最多允许 3 次 Repair。

未来改成：

```text
节点自己的 max_attempts
+
全局 total_repair_budget
+
deadline-aware
```

例如默认：

```text
单节点最多修复 1 次
全链最多修复 2 次
```

严格模式可放宽。

如果剩余时间不足：

```text
不要继续 repair
→ 直接失败
```

---

## 3.4 Viral Drama 不能继续等于整个 Engine

当前金手指、五槽位、广播剧音频约束、穿越规则在主流程里绑定过深。

改造后：

```text
这些能力保留
但归属 modes/viral_drama
```

而 State、LLM、Validator、Persistence、Template、Character、Job、Telemetry 提升为 Core。

---

# 4. 目标总体架构

```text
                        Client / AI Toy Backend
                                  │
                                  ▼
                           Story API Layer
                                  │
                                  ▼
                         Generation Orchestrator
                                  │
          ┌───────────────────────┼───────────────────────┐
          ▼                       ▼                       ▼
    Mode Resolver          Template Resolver       Character Resolver
          │                       │                       │
          └───────────────────────┼───────────────────────┘
                                  ▼
                           Story Engine Graph
                                  │
             ┌────────────────────┼────────────────────┐
             ▼                    ▼                    ▼
       Story Director       Mode Capabilities      Validators
             │                    │                    │
             └────────────────────┼────────────────────┘
                                  ▼
                          Content Form Writer
                                  │
                                  ▼
                              Package
                                  │
                                  ▼
                       Persistence / History
```

底层：

```text
PostgreSQL
Redis（阶段性引入）
Object Storage（需要音频/大型文件时）
LLM Providers
TTS Providers
```

---

# 5. 核心概念统一

## 5.1 Story Mode

决定：

> 故事怎样构思、怎样制造张力。

第一批建议：

```text
general
viral_drama
mystery
serialized
interactive（后续）
```

## 5.2 Content Form

决定：

> 最后交付的作品是什么形式。

例如：

```text
audio_drama
prose_story
novel
storytelling
standup
crosstalk
```

第一阶段只需要完整支持：

```text
audio_drama
prose_story
```

其他逐步扩展。

## 5.3 Story Template

决定：

> 某个故事的具体叙事骨架。

例如：

```text
错误认知翻转
身份错位
资源型逆袭
封闭空间谜题
先抑后扬
```

新增 Template **原则上不改代码**。

## 5.4 Rule Pack

决定：

> 这个故事必须遵守哪些创作和质量规则。

例如：

```text
viral_drama_core
strong_hook
character_choice
gadget_balance
continuity
mystery_clue
audio_adaptation
```

## 5.5 Capability

是 Engine 内可复用的生成能力，例如：

```text
gadget_design
behavior_design
fact_ledger
clue_ledger
chapter_planner
evidence_judge
tts_adaptation
```

Mode 本质上是：

```text
一组 Capability
+
一组默认 Rule Pack
+
一组默认 Prompt Policy
```

---

# 6. Mode、Template 与 Graph 的实现原则

## 6.1 新增 Template 不新增 Graph

例如新增“错误认知反转”，只需要写入 Story Template Repository：

```json
{
  "template_id": "OT_MYS_001",
  "name": "错误认知反转",
  "supported_modes": ["mystery", "viral_drama"],
  "genre_key": "mystery",
  "beats": [
    {
      "order": 1,
      "function": "hook",
      "instruction": "出现无法立即解释的异常"
    },
    {
      "order": 2,
      "function": "misjudgment",
      "instruction": "主角形成一个看似合理但错误的判断"
    },
    {
      "order": 3,
      "function": "cost",
      "instruction": "按错误判断行动并付出代价"
    },
    {
      "order": 4,
      "function": "reversal",
      "instruction": "新事实推翻原判断"
    }
  ]
}
```

无需复制 `graph.py`、新增整套 nodes 或新增一条工作流。

## 6.2 新增 Mode 才允许新增执行逻辑

只有当“执行机制”不同才新增 Mode。

例如 interactive 需要：

```text
interrupt
resume
checkpoint
重新规划
```

那么新增专属 Capability 是合理的。

---

# 7. 推荐项目目录重构

```text
StoryGenerate/
│
├── story_engine/
│   │
│   ├── api/
│   │   ├── routes_story.py
│   │   ├── routes_job.py
│   │   ├── routes_character.py
│   │   └── routes_template.py
│   │
│   ├── core/
│   │   ├── graph.py
│   │   ├── state.py
│   │   ├── context.py
│   │   ├── deadline.py
│   │   ├── errors.py
│   │   └── settings.py
│   │
│   ├── domain/
│   │   ├── request.py
│   │   ├── response.py
│   │   ├── character.py
│   │   ├── story_template.py
│   │   ├── story_plan.py
│   │   ├── memory.py
│   │   └── enums.py
│   │
│   ├── modes/
│   │   ├── base.py
│   │   ├── registry.py
│   │   ├── general/
│   │   ├── viral_drama/
│   │   └── mystery/
│   │
│   ├── forms/
│   │   ├── base.py
│   │   ├── registry.py
│   │   ├── audio_drama/
│   │   └── prose_story/
│   │
│   ├── templates/
│   │   ├── repository.py
│   │   └── matcher.py
│   │
│   ├── characters/
│   │   ├── repository.py
│   │   ├── resolver.py
│   │   └── runtime_builder.py
│   │
│   ├── memory/
│   │   ├── repository.py
│   │   ├── selector.py
│   │   └── summarizer.py
│   │
│   ├── validators/
│   │   ├── structural.py
│   │   ├── quality.py
│   │   └── evidence.py
│   │
│   ├── llm/
│   │   ├── base.py
│   │   ├── openai_compat.py
│   │   ├── router.py
│   │   └── mock.py
│   │
│   ├── persistence/
│   │   ├── models.py
│   │   ├── repositories/
│   │   ├── session.py
│   │   └── migrations/
│   │
│   ├── jobs/
│   │   ├── service.py
│   │   ├── worker.py
│   │   └── status.py
│   │
│   └── telemetry/
│
├── legacy_drama_engine/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── regression/
│   └── golden/
└── scripts/
```

迁移期间不要立即删除 `drama_engine/`，先作为 legacy baseline，便于对照、回归、回滚。

---

# 8. Mode Plugin 设计

建议定义统一接口：

```python
class StoryMode(Protocol):
    key: str

    def capabilities(self) -> list[str]:
        ...

    def default_rule_packs(self) -> list[str]:
        ...

    def prepare(self, state, runtime):
        ...

    def build_director_context(self, state):
        ...

    def validators(self):
        ...
```

注册：

```python
MODE_REGISTRY = {
    "general": GeneralMode(),
    "viral_drama": ViralDramaMode(),
    "mystery": MysteryMode(),
}
```

不要让 `if mode == ... elif ...` 散落在几十个文件中。

---

# 9. Viral Drama Mode 的保留与改造

现有项目的大量能力应成为 `modes/viral_drama`。

建议保留：

```text
金手指
价值层级
能力容量
非资源型约束
冲突层级升级
五槽位配方（作为一种 Recipe）
强 Hook
反转
Character Choice
Fact Ledger
Evidence Judge
```

但解除：

```text
所有故事必须恰好 5 个角色
所有故事必须有金手指
所有故事必须穿越
所有故事必须广播剧
```

这些硬绑定。

## 9.1 五槽位改为 Recipe

从 Engine Schema 降为 Viral Drama Recipe。

```json
{
  "recipe_id": "viral_five_slot_v1",
  "required": false,
  "slots": ["A", "B", "C", "D", "E"]
}
```

用户角色不足时：

- 可映射已有角色；
- 可补充临时角色；
- 也可以使用非五槽位 Recipe。

## 9.2 Gadget 改为 Capability

```json
{
  "story_mode": "viral_drama",
  "mode_config": {
    "enable_gadget": true
  }
}
```

其他 Mode 也可以开启。

---

# 10. Mode Resolver

优先级固定：

```text
1. 用户明确 story_mode
2. Story Template 限定 Mode
3. 自动识别
4. general
```

第一版自动识别不需要 LLM。

可以使用关键词 + genre + template + content_form + length 进行确定性打分。

后续如需要，再增加轻量模型分类器。

---

# 11. Content Form 体系

Mode 与作品形式解耦。

```json
{
  "story_mode": "viral_drama",
  "content_form": "audio_drama"
}
```

也可以：

```json
{
  "story_mode": "viral_drama",
  "content_form": "prose_story"
}
```

故事计划可以共用，只在 Writer / Renderer 阶段切换：

```text
Story Plan
  │
  ├─ Audio Drama Writer
  ├─ Prose Writer
  ├─ Novel Writer
  └─ Storytelling Writer
```

---

# 12. 推荐统一 Story Graph

```text
START
  ↓
Request Normalize
  ↓
Preflight
  ↓
Mode Resolver
  ↓
Content Form Resolver
  ↓
Template Resolver
  ↓
Character Resolver
  ↓
Memory Resolver
  ↓
Mode Preparation
  │
  ├─ optional Gadget
  ├─ optional Behavior
  ├─ optional Special Rules
  └─ ...
  ↓
Story Director
  ↓
Plan Gate
  ↓
Length Strategy Resolver
  │
  ├─ Short / Standard
  │       ↓
  │     Writer
  │
  └─ Long / Serialized
          ↓
       Chapter/Episode Plan
          ↓
       Chapter/Episode Writer
          ↓
       Continuity
  ↓
Quality Gate
  ↓
Package
  ↓
Persistence
  ↓
END
```

---

# 13. 角色系统详细设计

## 13.1 Canon 与 Runtime 必须分离

推荐：

```python
class CharacterTemplate(BaseModel):
    character_id: str
    name: str
    gender: str
    age: int | None
    age_band: str
    species: str | None
    character_type: str
    identity: str
    appearance: dict
    abilities: list[str]
    immutable_facts: list[str]
    world_id: str | None
    runtime_defaults: dict
```

Canon 原则上不得由 Story Director 修改。

## 13.2 Runtime Character State

```python
class RuntimeCharacterState(BaseModel):
    role_id: str
    character_id: str | None
    personality: list[str]
    goal: str
    motivation: str | None
    fear: str | None
    stance: str | None
    dialogue_style: str | None
    behavior_rules: list[dict]
    relationship_states: list[dict]
    arc: dict | None
```

## 13.3 合并优先级

动态字段：

```text
用户本次明确填写
>
角色模板 runtime_defaults
>
历史 Character Memory
>
Story Director 自动生成
```

固有字段 Character Canon 拥有最高约束等级。

如果用户要求与 Canon 冲突：

```text
返回 CHARACTER_CANON_CONFLICT
```

未来可增加显式 `allow_non_canon=true`，默认 false。

---

# 14. Character Resolver 流程

```text
输入 characters
↓
根据 character_id 查询模板
↓
加载 Canon
↓
合并用户本次 Runtime 字段
↓
加载有限相关 Memory
↓
生成缺失 Runtime 字段
↓
如果 character_count > 已提供角色数
→ 自动补临时角色
↓
输出 Resolved Characters
```

---

# 15. 数据库选型

## 15.1 主数据库：PostgreSQL

推荐 PostgreSQL，原因：

- 成熟稳定；
- JSONB 适合半结构化 Template / Runtime State；
- 强事务；
- 可用于任务状态；
- 支持全文检索；
- 后续可直接安装 pgvector；
- 不需要为了角色库、模板库、记忆库同时维护多个数据库。

## 15.2 ORM 与迁移

推荐：

```text
SQLAlchemy 2.x
Alembic
```

所有表结构修改必须有 migration。

禁止生产环境启动时自动 `create_all()` 代替版本迁移。

## 15.3 Redis

第一阶段非强制。

以下需求出现后引入：

```text
长任务队列
分布式锁
热点 Template 缓存
速率限制
Job Progress
```

推荐：

```text
PostgreSQL = 真相来源
Redis = 临时加速层
```

## 15.4 Vector DB

第一阶段不建议单独部署 Milvus / Qdrant。

需要语义记忆和模板检索时先使用：

```text
PostgreSQL + pgvector
```

数据量显著增长后再评估独立向量数据库。

---

# 16. 第一批数据库表

## 16.1 character_template

```text
id
character_id
name
version
status
canon_json JSONB
runtime_defaults_json JSONB
created_at
updated_at
```

## 16.2 story_template

```text
id
template_id
name
version
status
supported_modes
supported_forms
genre_key
tags
template_json JSONB
quality_score
usage_count
created_at
updated_at
```

新增故事模板时写数据库即可，不重新发布 Engine。

## 16.3 story_job

解决“用户不能空等”：

```text
job_id
request_id
status
progress
current_stage
input_json
resolved_mode
resolved_template_id
error_code
error_message
created_at
started_at
finished_at
deadline_at
```

status：

```text
pending
running
succeeded
failed
cancelled
timeout
```

## 16.4 story_record

```text
story_id
job_id
mode
content_form
template_id
title
summary
content
output_json
created_at
```

## 16.5 character_memory

```text
memory_id
character_id
memory_type
content
importance
source_story_id
embedding（后续）
created_at
expires_at
```

Memory 不能直接修改 Canon。

## 16.6 generation_trace

```text
trace_id
job_id
node
model
attempt
latency_ms
input_tokens
output_tokens
status
error_code
created_at
```

## 16.7 quality_result

```text
id
story_id
validator
score
passed
details_json
created_at
```

---

# 17. 模型记忆设计

“模型记忆”不要理解为把历史故事全部塞进 Prompt。

应分为：

```text
Character Canon
长期不变
        ↓
Character Memory
长期经历摘要
        ↓
Current Story State
本次故事状态
```

每次生成仅选择 Top N 相关记忆。

第一阶段可用：

```text
importance + recency + 关键词
```

后续增加 pgvector similarity。

---

# 18. 历史记录与可复现性

每个 Story Job 必须记录：

```text
engine_version
mode
content_form
template_id + version
rule_pack versions
prompt version
model mapping
input snapshot
character snapshot
output
quality result
token usage
latency
```

这样以后可以复盘“为什么昨天生成得好，今天变差”。

---

# 19. 故事吸引力机制

不能仅依靠“Writer 写得好”。

建议分四层。

## 19.1 Mode Rules

例如 Viral Drama：

```text
Hook
明确目标
冲突
阶段收益
代价
反转
高潮
尾钩
```

## 19.2 Character Behavior

至少检测：

```text
角色是否有具体目标
是否存在有代价的选择
核心角色是否至少一次违背短期自身利益
关系变化是否绑定事件
```

保留当前 Behavior Card 思路。

## 19.3 Story Plan Gate

Writer 前必须先检查 Plan：

```text
Hook 是否存在
核心问题是否明确
冲突是否升级
是否有选择
选择是否产生后果
是否存在反转/新信息
高潮是否回应核心冲突
结尾是否完成承诺
```

Plan 不合格时最多 Repair 一次，避免生成完几千字才发现结构有问题。

## 19.4 Final Quality Gate

优先使用确定性检查：

```text
空输出
长度严重不足
章节缺失
角色丢失
结构不完整
JSON 无法解析
重复段落
明显截断
```

语义 Judge：

- 仅用于真正需要语义判断的项目；
- 不要每个小节点都调用强模型；
- 受全局 Deadline 控制。

---

# 20. “吸引力”建议验收指标

## 自动指标

```text
Hook Presence
Plan Completeness
Conflict Escalation
Meaningful Choice
Choice Consequence
Reversal / New Information
Climax Presence
Continuity
Character Coverage
Output Completeness
```

## 人工 A/B

建议至少评分：

```text
开头吸引力
想不想继续
剧情推进速度
冲突强度
角色辨识度
角色主动性
反转有效性
高潮
对白/文风
整体娱乐性
```

不要只统计规则通过率，因为“规则全绿 ≠ 一定好看”。

---

# 21. 性能与失败策略

这是本次二开的最高优先级之一。

## 21.1 引入 DeadlineContext

每个请求创建：

```python
DeadlineContext(
    started_at=...,
    deadline_at=...
)
```

任何节点执行前检查 `remaining_time`，不足时直接中止。

## 21.2 建议默认预算

以下为建议默认值，必须配置化，不写死。

### 标准短/中故事同步模式

```text
global_deadline_sec = 120
```

### 单次 LLM 调用

建议分别配置：

```text
connect timeout
read timeout
total node budget
```

避免单一 `timeout=120`。

### Retry

```text
网络错误/429/5xx：最多 1 次快速重试
业务 JSON 错误：结构化重试最多 1 次
明确 4xx：不重试
```

## 21.3 全局 Repair Budget

```text
max_total_repairs = 2
max_repairs_per_stage = 1
```

严格质量模式可以单独增加。

## 21.4 长篇采用异步 Job

对于长篇小说、大量章节、多集连续剧、预计输出超出同步预算的任务，不要让 HTTP 请求一直挂着。

改成：

```text
POST /stories/generate
↓
立即返回 202 + job_id
↓
后台 Worker
↓
GET /story-jobs/{job_id}
```

可进一步支持 SSE / WebSocket Progress。

用户会看到：

```text
正在规划
正在生成第 2/8 章
正在校验
```

而不是页面无反馈。

---

# 22. 失败必须明确

推荐错误码：

```text
INVALID_INPUT
MODE_NOT_SUPPORTED
CONTENT_FORM_NOT_SUPPORTED
TEMPLATE_NOT_FOUND
CHARACTER_NOT_FOUND
CHARACTER_CANON_CONFLICT
MODEL_NOT_CONFIGURED
MODEL_UNAVAILABLE
MODEL_TIMEOUT
MODEL_RATE_LIMITED
MODEL_INVALID_OUTPUT
PLAN_VALIDATION_FAILED
QUALITY_GATE_FAILED
GENERATION_TIMEOUT
GENERATION_CANCELLED
INTERNAL_ERROR
```

失败响应：

```json
{
  "status": "failed",
  "request_id": "...",
  "job_id": "...",
  "error": {
    "code": "MODEL_TIMEOUT",
    "message": "故事生成模型响应超时，请稍后重试。"
  }
}
```

禁止：

```json
{
  "status": "success",
  "story": ""
}
```

---

# 23. 空输出防护

Engine 最终输出前强制：

```text
story != null
content != ""
content 长度达到最低阈值
没有明显截断
```

任何一项失败：

```text
→ failed
```

不能返回 `success_with_warning`。

---

# 24. 模型调用策略优化

继续保留现有 Tier Router。

例如：

```text
Mode 分类
→ 规则/代码优先，不调用 LLM

Template 匹配
→ 数据库规则，不调用 LLM

Character 查询
→ 数据库，不调用 LLM

Story Director
→ reasoning

Hook / 高杠杆创意
→ strong

结构化填充
→ cheap

Writer
→ strong / reasoning

确定性校验
→ 不调用 LLM
```

目标：

> **把 LLM 调用集中到真正需要创造力和语义判断的节点。**

---

# 25. 缓存策略

可缓存：

```text
模板查询
Rule Pack
Mode Definition
角色模板
固定 Prompt 片段
相同输入下的某些结构化规划
```

缓存键继续沿用当前良好思路：

```text
node + prompt_version + rule_version + input_hash
```

不要缓存强依赖用户实时状态的 Story Runtime，除非显式开启。

---

# 26. API 建议

## 26.1 生成故事

```text
POST /api/v1/stories/generate
```

核心输入：

```json
{
  "request_id": "uuid",
  "story_mode": "auto",
  "content_form": "audio_drama",
  "characters": [],
  "character_count": 3,
  "story_template": null,
  "premise": "...",
  "outline": "",
  "requirements": [],
  "genre_key": "mystery",
  "tone_hint": "",
  "target_duration_minutes": 10,
  "ending_preference": "open"
}
```

## 26.2 查询 Job

```text
GET /api/v1/story-jobs/{job_id}
```

## 26.3 查询 Story

```text
GET /api/v1/stories/{story_id}
```

## 26.4 Character Template

```text
GET    /api/v1/characters
POST   /api/v1/characters
GET    /api/v1/characters/{character_id}
PUT    /api/v1/characters/{character_id}
```

## 26.5 Story Template

```text
GET    /api/v1/story-templates
POST   /api/v1/story-templates
GET    /api/v1/story-templates/{template_id}
PUT    /api/v1/story-templates/{template_id}
```

---

# 27. 长度策略

不要把短篇、中篇、长篇写成不同 Mode。

使用：

```text
content_form + length_profile + target words / duration
```

Length Strategy Resolver：

```text
短：
单 Plan + 单 Writer

中：
Plan + Chapter Plan + 多 Chapter

长：
Story Bible
+ Volume Plan
+ Chapter Plan
+ Ledger
+ 多轮 Writer
+ Continuity
```

这样兼容当前长时长能力。

---

# 28. 长篇生成必须保留 Checkpoint

长篇过程中：

```text
Story Bible
Outline
Character Runtime
Fact Ledger
已完成章节
```

都应写 Checkpoint。

失败恢复时：

```text
从最近完成节点继续
```

而不是从头生成。

---

# 29. 分阶段执行方案

以下每个阶段都必须做到：

```text
独立可测试
独立可上线
可回滚
```

禁止进行一次“大爆炸式重构”。

## Phase 0：冻结基线与回归资产

### 目标

在改造前明确“当前哪些东西不能被无意破坏”。

### 工作项

1. 固定当前 `0.2.1` 为 baseline tag；
2. 保留当前 verify matrix；
3. 收集真实模型下至少一批可接受故事作为 golden samples；
4. 将输入、输出、模型、耗时、规则版本记录下来；
5. 建立 `tests/regression/legacy/`。

### 验收

```text
现有 verify matrix 全部通过
现有 CLI 可运行
现有 Viral Drama 输出可复现
```

---

## Phase 1：先解决“空等”和失败不明确

**这是优先上线阶段。**

### 改造内容

1. `APP_ENV`；
2. prod 禁止 Mock Fallback；
3. DeadlineContext；
4. per-node timeout；
5. 全局 retry / repair budget；
6. 空输出强制失败；
7. 标准错误码；
8. Job Status；
9. Telemetry 增加 latency / failure。

### 主要修改位置

```text
llm/openai_compat.py
llm/base.py
core/deadline.py（新增）
core/errors.py（新增）
graph.py
contracts.py
state.py
cli.py
```

### 验收用例

- 模型地址不可达：必须 failed，不能 Mock 成功；
- LLM 返回空：必须 failed；
- LLM 返回非法 JSON：允许一次结构化修复，仍失败则 failed；
- 整体 Deadline 到：返回 `GENERATION_TIMEOUT`；
- 正常路径：现有 Viral Drama 质量不能明显退化。

---

## Phase 2：抽取 Story Engine Core

### 目标

不改变主要业务效果，先把当前代码从：

```text
Viral Drama Engine
```

抽成：

```text
Story Engine Core + Viral Drama Mode
```

### 工作项

1. 新建 `story_engine/core`；
2. 新建 Mode Registry；
3. 把现有金手指、五槽位、穿越规则移动到 `modes/viral_drama`；
4. LLM / State / Runtime / Telemetry 提升为 Core；
5. 保持 Legacy CLI Adapter。

### 验收

旧命令仍能通过 Adapter 调用新引擎，并得到与原逻辑等价的 Viral Drama。

---

## Phase 3：角色“半固定 + 动态演绎”

**这是领导要求的核心阶段。**

### 工作项

1. 新建 CharacterTemplate；
2. Canon / Runtime 分离；
3. Character Resolver；
4. 支持已有角色；
5. 支持部分 Runtime 用户输入；
6. 缺失 Runtime 由 Director 补；
7. 支持角色数量不足时自动补临时角色；
8. 禁止 Director 修改 Canon；
9. 将现有 Behavior Card 接入 Runtime Character。

### 验收

- 角色只填写固有信息：可以正常生成，Runtime 自动补齐；
- 用户填写 personality：必须优先保留；
- 用户不填写 goal：Director 自动生成；
- 用户尝试改固定性别/身份：默认拒绝或明确 non-canon；
- 同一角色两次不同故事：Canon 相同，Runtime 可以不同。

---

## Phase 4：数据库化 Template、Character、History

### 目标

从文件型资产逐步变成后台可运营资产。

### 工作项

1. PostgreSQL；
2. SQLAlchemy；
3. Alembic；
4. Repository Interface；
5. CharacterTemplate 表；
6. StoryTemplate 表；
7. StoryJob；
8. StoryRecord；
9. GenerationTrace；
10. QualityResult。

Rule Library 第一阶段可以继续读 JSON。

不要一次全部迁数据库。

### 验收

后台插入一条新 Story Template：

```text
无需修改 Python
无需新增 Graph
无需重新发布 Engine
```

即可被 Template Resolver 使用。

---

## Phase 5：General Mode + Template Resolver

### 目标

证明“一个 Core 支持多 Mode”。

新增：

```text
general
```

General Mode：

```text
不强制金手指
不强制五槽位
保留 Character Behavior
使用轻量 Plan Gate
```

同时实现 Mode Resolver 和 Template Matcher。

### 验收

同一 API：

```text
viral_drama
general
auto
```

都可以使用。

---

## Phase 6：新增 Prose Story Content Form

### 目标

证明 Mode 和 Content Form 已真正解耦。

同一个 Viral Drama Plan：

```text
content_form=audio_drama
```

输出剧本；

```text
content_form=prose_story
```

输出叙事短篇。

Story Director 共用，Writer / Validator 分流。

---

## Phase 7：长篇与异步任务

### 工作项

1. Length Strategy Resolver；
2. Chapter / Episode Planner；
3. PostgreSQL Checkpoint；
4. Worker；
5. Redis（如需要）；
6. Job Progress；
7. Cancel；
8. Resume；
9. Continuity；
10. Fact Ledger。

### 验收

长篇任务提交后快速得到 `job_id`；中间可查询 stage/progress；失败明确 failed；服务重启后可从 Checkpoint 恢复。

---

## Phase 8：Character Memory

### 工作项

1. character_memory 表；
2. 记忆摘要；
3. Memory Selector；
4. Recency + Importance；
5. 记忆注入上限；
6. 记忆冲突检查；
7. 后续 pgvector。

### 关键规则

Memory：

```text
不能覆盖 Canon
不能无限增长 Prompt
不能默认把所有历史故事注入
```

---

## Phase 9：扩展更多 Mode / Form

随后可以独立扩展：

```text
mystery
serialized
interactive
novel
storytelling
standup
crosstalk
```

新增原则：

```text
仅结构差异 → Template
仅规则差异 → Rule Pack
生成机制差异 → Capability / Mode
最终文体差异 → Content Form
```

---

# 30. 上线策略

每个新能力增加 Feature Flag：

```text
ENABLE_NEW_ENGINE_CORE
ENABLE_DB_TEMPLATE
ENABLE_CHARACTER_RUNTIME
ENABLE_AUTO_MODE
ENABLE_GENERAL_MODE
ENABLE_PROSE_FORM
ENABLE_LONGFORM_JOB
ENABLE_CHARACTER_MEMORY
```

支持旧引擎与新引擎短期并存。

当新引擎指标稳定后，再删除旧路径。

---

# 31. A/B 与灰度

对相同 Prompt：

```text
Legacy Viral Drama
vs
New Viral Drama Mode
```

统计：

```text
成功率
P50 延迟
P95 延迟
Token
人工吸引力评分
空输出率
Repair 次数
```

新架构不能只以“测试通过”作为上线标准。

---

# 32. 关键 SLO 建议

以下值配置化。

核心指标：

```text
success_rate
empty_output_rate
timeout_rate
p50_latency
p95_latency
avg_model_calls
avg_repairs
avg_token_cost
quality_score
```

其中硬红线：

```text
success 状态下 empty_output_rate = 0
```

---

# 33. Codex 开发约束

交给 Codex 等工具开发时，应把下面规则放进任务说明。

## 33.1 一次只做一个 Phase

不要一次把整个仓库重写。

每个 Phase 独立 PR / Commit。

## 33.2 改代码前先读

必须先阅读：

```text
README.md
workflow-spec.json
graph.py
state.py
schemas.py
contracts.py
config.py
llm/
validators/
verify_matrix.py
```

## 33.3 不得删除 Legacy Regression

新测试增加，不替换已有回归。

## 33.4 不允许静默兼容错误

如果属于业务必需字段，应明确报错。

## 33.5 不允许生产 Mock Fallback

Mock 只能用于 unit test、integration test、demo。

## 33.6 Prompt 与业务规则分离

继续沿用：

```text
Rule Repository
→ Prompt Builder
```

## 33.7 所有新 LLM 节点必须声明

```text
role
model tier
temperature
max_tokens
timeout
retry policy
structured output schema
```

## 33.8 所有循环必须有

```text
max_attempts
deadline check
exit condition
```

## 33.9 所有成功输出必须通过 Output Contract

不能“某个 Node 有 text 就直接 success”。

必须：

```text
Package Validator
→ success
```

---

# 34. Codex 第一批任务拆解

建议顺序严格执行。

## Task 1：生产环境 Fail-Fast

实现：

```text
APP_ENV
禁止 prod mock fallback
标准 ModelError
```

## Task 2：Deadline

新增：

```text
DeadlineContext
remaining_seconds()
ensure_time()
```

所有 LLM / Repair Node 接入。

## Task 3：Output Guard

新增：

```text
validate_delivery()
```

检查非空、结构完整、长度、截断。

## Task 4：Job Model

先使用内存 Job Store，不要一开始就数据库化所有东西。

验证状态机：

```text
pending
running
succeeded
failed
timeout
```

## Task 5：抽 Mode Registry

将当前逻辑注册成 `viral_drama`，确保行为不变。

## Task 6：Character Canon / Runtime

完成领导要求。

## Task 7：PostgreSQL Repository

再将 Job、Character、Template、History 落库。

---

# 35. 不建议现在做的事情

为了控制复杂度，第一轮不要同时做：

```text
几十种 Mode
几十种 Content Form
复杂向量数据库
知识图谱
多 Agent 群体自治
角色独立 Agent
全自动 Prompt 优化
全链路 LLM Judge
```

先把：

```text
Viral Drama
General
Audio Drama
Prose Story
Character Template
Story Template
Database
Fail-Fast
```

做稳。

---

# 36. 推荐第一版正式能力范围

建议第一个可正式使用的新版本支持：

```text
Mode:
- viral_drama
- general
- auto

Content Form:
- audio_drama
- prose_story

Length:
- short
- medium
- long（异步）

Character:
- 固定 Canon
- 可选 Runtime
- 自动补 Runtime
- 自动补临时角色

Template:
- DB 管理
- 可在线新增
- 无需新增代码路径

History:
- 保存 Request / Output / Trace

Failure:
- 明确 failed
- 全局 Deadline
- 禁止空 success
```

这已经足以取代现有 Dify 故事生成主链。

---

# 37. 最终目标结构

```text
                  Narrative Content Platform
                            │
                    Story Generation API
                            │
                       Story Engine
                            │
      ┌─────────────────────┼─────────────────────┐
      ▼                     ▼                     ▼
  Mode System          Character System      Template System
      │                     │                     │
      ▼                     ▼                     ▼
 Viral/General/...       Canon+Runtime       Templates+Rules
      │                     │                     │
      └─────────────────────┼─────────────────────┘
                            ▼
                       Story Director
                            ▼
                        Story Plan
                            ▼
                  Content Form Renderer
             ┌──────────────┼───────────────┐
             ▼              ▼               ▼
        Audio Drama       Prose           Novel...
             │              │
             └──────────────┼───────────────┘
                            ▼
                       Quality Gate
                            ▼
                   History / Memory / DB
```

---

# 38. 最终执行原则

项目后续开发应长期遵守以下十条：

1. **吸引力优先，但套路数据化、规则化，不把套路硬编码成唯一故事。**
2. **Mode 是生成机制，Template 是数据，不为每个模板开发新工作流。**
3. **Content Form 与 Story Mode 分离。**
4. **角色 Canon 固定，Runtime 可演绎。**
5. **模型调用必须有 Deadline、Retry Budget 与 Repair Budget。**
6. **生产环境绝不允许 Mock 冒充真实生成。**
7. **失败必须明确，空故事绝不能 success。**
8. **PostgreSQL 作为持久化单一真相来源，Redis 只做加速。**
9. **每个阶段独立上线、独立验证、可回滚。**
10. **先复用当前有效能力，再逐渐抽象，不进行一次性重写。**

---

# 39. 推荐开发启动顺序

```text
Phase 0
冻结 0.2.1 回归基线
        ↓
Phase 1
Fail-Fast + Deadline + Output Guard
        ↓
Phase 2
Story Engine Core + Viral Drama Mode
        ↓
Phase 3
Character Canon + Runtime
        ↓
Phase 4
PostgreSQL + Template/History
        ↓
Phase 5
General Mode
        ↓
Phase 6
Prose Story Form
        ↓
Phase 7
长篇异步 Job + Checkpoint
        ↓
Phase 8
Character Memory
        ↓
Phase 9
更多 Mode / Form
```

完成 Phase 1 后即可获得明显的线上可靠性提升。

完成 Phase 3 后即可满足领导强调的“角色部分确定、每次故事可继续扩展”。

完成 Phase 4～6 后，StoryGenerate 将从“固定广播剧生成器”基本完成向“模块化 Story Engine”的转变。

后续新增模板主要进入数据库，不再复制工作流；新增 Mode 和 Content Form 时，也只在对应插件模块内扩展，不影响整个系统。
