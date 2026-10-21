# StoryGenerate — AI 故事生成引擎 v0.2.1

**正式交付入口：[故事生成模块技术交接包](docs/handoff/README.md)**。包含完整26操作API手册、8套人工演示、7份真实故事全文、播放中改剧情边界、质量与成本评估及部署说明。基线eeb99ae；HTTP服务当前仅接受audio_drama，最低年龄档14-17；真实TTS与硬件由其他团队负责。

Phase 11 产品后端：复用已验收四操作引擎，新增 PostgreSQL 异步 HTTP 服务、版本化角色库和演绎任务适配器。参见 [部署与 API 调用](docs/phase11/DEPLOYMENT.md)、[OpenAPI](docs/phase11/openapi.json)、[TTS/客户端联调](docs/phase11/TTS_CLIENT_INTEGRATION.md)。真实音频与硬件播放不在本轮范围。

[Phase 11 最终验收与成本报告](docs/phase11/ACCEPTANCE.md)：685 项工程测试通过，最终容器34项合同测试通过，四操作真实业务链路完成；保留全部失败及增量修复证据。

> **下方包含早期引擎路线与历史能力说明；当前四操作服务的验收状态和接口边界以[正式交付包](docs/handoff/README.md)为准。**

一个完整的 AI 故事生成引擎，将爆款故事的结构规律（角色槽位、情节节拍、人物选择规律、事实账本、证据评审）编译成可执行的确定性流水线。支持多故事模式（病毒短剧 / 通用 / 悬疑推理解谜 / 连续剧连载）、多内容形态（广播剧 / 散文体 / 小说 / 口述故事 / 单口喜剧 / 相声）、Long-form 异步任务、SQL 持久化断点续传、角色记忆系统。

---

## 项目结构

```
故事生成0.2.1/
├── 1-总纲文档/                      # 总体方案设计文档
│   └── AI广播剧生成引擎_完整方案.html
│
├── 2-工作流契约/                      # 各 Phase 开发提示词契约
│
├── 3-规则库/                          # 引擎规则数据（JSON，只读装载）
│   ├── drama-formula-library.json     # 五槽位 / 配方 / 节拍表 / 钩子 / R01–R13
│   ├── 穿越剧引擎规则.json             # K01–K18 / 价值层级 / 张力曲线
│   └── 人物与连续性规则.json           # CH01–CH06 / RL01–RL05 / FC01–FC06 / EV01–EV06
│
├── 4-引擎代码/                         # ★ 可执行引擎本体
│   ├── drama_engine/                  # 核心引擎包（~150 模块）
│   │   ├── graph.py                   # ★ 主图编排：条件边 / 并行 fan-out
│   │   ├── state.py                   # 状态定义与四种 reducer 语义
│   │   ├── schemas.py                 # Pydantic 领域模型
│   │   ├── continuity.py              # 本集切片 + 应然清单构建
│   │   ├── contracts.py               # 五个 Protocol 预留接口 + Runtime 装配
│   │   ├── config.py                  # 规则库装载 / 版本指纹
│   │   ├── prompts.py                 # 提示词动态组装
│   │   ├── async_runtime.py           # ★ Phase 7: 异步任务运行时（submit / cancel / resume）
│   │   ├── longform_executor.py       # ★ Phase 7: Long-form 引擎（checkpoint / restart）
│   │   ├── longform_planner.py        # Long-form 全局规划器
│   │   ├── longform.py                # Long-form 配置与模型
│   │   ├── modes/                     # ★ Phase 9: 故事模式系统
│   │   │   ├── base.py                #   ModeProfile / Capability / StoryMode Protocol
│   │   │   ├── registry.py            #   ModeRegistry 单例
│   │   │   ├── clue_ledger.py         #   Mystery: ClueLedger 线索账本
│   │   │   ├── rule_packs.py          #   Mode-specific 规则组
│   │   │   ├── viral_drama/           #   病毒短剧模式（默认）
│   │   │   ├── general/               #   通用模式
│   │   │   ├── mystery/               #   悬疑推理解谜模式
│   │   │   └── serialized/            #   连续剧/连载模式
│   │   ├── forms/                     # ★ Phase 9: 内容形态系统
│   │   │   ├── registry.py            #   ContentFormRegistry 单例
│   │   │   ├── handlers.py            #   6 个 Form Handler + Validator
│   │   │   ├── audio_drama.py         #   广播剧
│   │   │   ├── prose_story.py         #   散文叙事
│   │   │   ├── novel.py               #   小说
│   │   │   ├── storytelling.py        #   口述故事
│   │   │   ├── standup.py             #   单口喜剧
│   │   │   └── crosstalk.py           #   相声
│   │   ├── content_forms/             # Phase 6: 旧 Content Form 路由（兼容层）
│   │   ├── characters/                # ★ Phase 3/8: 角色系统
│   │   │   ├── models.py              #   CharacterCanon / CharacterRuntime
│   │   │   ├── memory.py              #   CharacterMemory 领域模型
│   │   │   ├── memory_selector.py     #   MemorySelector（预算控制）
│   │   │   ├── memory_guard.py        #   MemoryCanonGuard
│   │   │   ├── memory_summarizer.py   #   记忆摘要器
│   │   │   ├── memory_service.py      #   记忆服务编排
│   │   │   ├── resolver.py            #   角色解析器
│   │   │   └── overlay.py             #   角色覆盖
│   │   ├── persistence/               # ★ Phase 4/7: 持久化层
│   │   │   ├── database.py            #   SQLAlchemy Engine / Session
│   │   │   ├── models.py              #   ORM 模型
│   │   │   ├── repository.py          #   业务 Repository
│   │   │   ├── postgres_repos.py      #   Postgres Job / Checkpoint Repository
│   │   │   ├── memory_repo.py         #   角色记忆 Repository
│   │   │   ├── repo_factory.py        #   Repository 工厂
│   │   │   └── settings.py            #   持久化配置
│   │   ├── templates/                 # Phase 5: 故事模板系统
│   │   ├── nodes/                     # 立项 / 逐集 / 归约 三层节点
│   │   ├── validators/                # 校验器（确定性 / 语义 / 证据）
│   │   ├── llm/                       # 模型路由 + Mock + 适配器
│   │   ├── core/                      # 核心基础（错误码 / 截止时间 / Job Repo）
│   │   └── cli.py                     # 命令行入口
│   └── requirements.txt
│
├── 5-回归验证/                         # 手动回归记录
│
├── 6-实测产物/                         # 引擎运行记录
│
├── alembic/                           # ★ Phase 4/7: 数据库迁移
│   ├── versions/
│   │   ├── 001_phase8_precursor.py    #   Phase 4 baseline
│   │   └── 002_phase8_character_memory.py  # Phase 8 migration
│   └── env.py
│
├── tests/                             # ★ 测试套件（428 tests）
│   ├── unit/
│   │   ├── test_phase1.py             # Phase 1: Fail-Fast + Deadline
│   │   ├── test_phase2_mode.py        # Phase 2: Viral Drama Mode
│   │   ├── test_phase3_character.py   # Phase 3: Character Canon
│   │   ├── test_phase5_mode_template.py # Phase 5: General Mode
│   │   ├── test_phase6_*.py           # Phase 6: Content Form ×4
│   │   ├── test_phase7_*.py           # Phase 7: Long-form ×6
│   │   ├── test_phase8_*.py           # Phase 8: Character Memory
│   │   ├── test_phase9_*.py           # Phase 9: Mode/Form ×4
│   │   ├── conftest_persistence.py    #   SQLite-backed Checkpoint Repo
│   │   └── persistent_store.py        #   测试持久化工具
│   ├── regression/golden/             # Golden 回归基线
│   └── benchmark/                     # 性能基线
│
├── scripts/                           # 辅助脚本
├── docs/                              # 开发提示词文档
├── alembic.ini
├── docker-compose.yml                 # PostgreSQL 容器
└── README.md                          # ← 本文件
```

---

## Roadmap 完成状态

| Phase | 名称 | 状态 |
|-------|------|------|
| 0 | Baseline / Regression Freeze | ✅ |
| 1 | Fail-Fast + Deadline + Output Guard | ✅ |
| 2 | Story Engine Core + Viral Drama Mode | ✅ |
| 3 | Character Canon + Runtime | ✅ |
| 4 | PostgreSQL + Template / History | ✅ |
| 5 | General Mode + Template Resolver | ✅ |
| 6 | Prose Story Content Form | ✅ |
| 7 | Long-form Async Job + Checkpoint | ✅ |
| 8 | Character Memory | ✅ |
| 9 | More Mode / Form | ✅ |

**ORIGINAL PHASE 0–9 ROADMAP COMPLETE ✅**

---

## 快速开始

### 环境要求

- Python 3.10+
- 依赖：`langgraph`, `langchain-core`, `pydantic`, `sqlalchemy`, `httpx`

```bash
pip install -r 4-引擎代码/requirements.txt
```

新工作流推荐从仓库根目录安装：`python -m pip install -e '.[dev]'`。

### 运行测试

```bash
# 全部单元测试（包含原有 428 项）
python -m pytest tests/unit/ -v

# 单 Phase 测试
python -m pytest tests/unit/test_phase9_more_mode_form.py -v
```

### 命令行生成

```bash
cd 4-引擎代码
python -m drama_engine.cli \
  --idea "穿越成随军杂役，我带着青霉素与军粮辅佐主帅北伐" \
  --assets 青霉素,军粮 \
  --episodes 6 \
  --duration 180 \
  --out ../6-实测产物/run.json
```

### 接入真实大模型

```bash
set DRAMA_LLM_VENDOR=deepseek
set DRAMA_LLM_API_KEY=sk-xxxx
python -m drama_engine.cli --idea "..." --provider env
```

引擎内置 `MockLLMProvider`，**无网络、无密钥、无 GPU 也能端到端跑通**。

### 获批大纲工作流（新增）

`drama_engine.workflow` 提供框架无关的 `StoryWorkflowService` 与 JSON Schema。正式 Create 流程为
`prepare_story_plan → revise_story_plan（可选）→ approve_story_plan → generate_from_approved_plan`。
生成入口只接收已持久化审批快照的 Job ID；交付版本须通过逐句演绎、集数、Mode、大纲与模型质量门。
`continue/revise/remix` 也先产出 Plan，版本谱系保留来源 ID；旧 `run_pipeline` 是兼容入口。

正式故事执行器使用 `ApprovedPlanLLMExecutor`、`LLMPlanGenerator` 和 `SqlAlchemyWorkflowRepository`。
将 `DRAMA_LLM_API_KEY`、`DATABASE_URL` 注入运行进程后，先执行 `alembic upgrade head`，
应用服务会在每次状态转移后提交 SQLAlchemy Session；调用方不要把同一 Session 用于其它未提交业务写入。
`content_form` 首发仅接受 `audio_drama`；
`120/180/300` 秒为产品值，`90/150` 秒仍兼容。SQLite 只用于快速测试。

真实模型网关在 2026-10-08 探测时，原 Fast 模型 `deepseek-v4-flash` 返回 HTTP 404。
当前验收显式设置 `STORY_LLM_FAST_MODEL=qwen/qwen3.6-flash`；其余 Tier 沿用既定映射。
运行 `python tests/acceptance/probe_models.py` 可重新探测五个模型，
`python tests/acceptance/real_create_matrix.py` 可执行 12 个固定真实 Create 样本。
这两个脚本均要求真实 API Key，不会回落 Mock。PostgreSQL 事务测试需要 `TEST_DATABASE_URL`。

---

## 核心架构

### 故事模式（Story Mode）vs 内容形态（Content Form）

```
Mode 决定故事怎样构思、推进和制造张力
Content Form 只决定最终写成什么形态
```

| 故事模式 | Key | 默认 Form | 核心特征 |
|----------|-----|-----------|----------|
| 病毒短剧 | `viral_drama` | `audio_drama` | 金手指、五槽位、高强度钩子 |
| 通用模式 | `general` | `audio_drama` | 自由体裁、无强约束 |
| 悬疑推理 | `mystery` | `prose_story` | ClueLedger 线索账本、公平解谜 |
| 连续剧 | `serialized` | `prose_story` | 跨集主线、未解决线程、悬念 |

| 内容形态 | Key | 输出特征 |
|----------|-----|----------|
| 广播剧 | `audio_drama` | 多角色对话 + 音效标记 |
| 散文叙事 | `prose_story` | 第三人称叙事 |
| 小说 | `novel` | 章节/场景组织、对白嵌入 |
| 口述故事 | `storytelling` | 讲述者主导、叙白交错 |
| 单口喜剧 | `standup` | 单人表演、setup-punchline |
| 相声 | `crosstalk` | 双人逗捧、exchange 递进 |

模式与形态**正交组合**：`mystery + novel`、`serialized + prose_story`、`general + standup` 均合法。

### Mode/Form 注册表

- `ModeRegistry`（`modes/registry.py`）— 单例，`_bootstrap()` 注册 4 个内建 Mode
- `ContentFormRegistry`（`forms/registry.py`）— 单例，`_bootstrap()` 注册 6 个内建 Form

两个注册表均支持 `get(key)`、`available_modes()`/`available_forms()`、`register()`。未知 key 抛出 `MODE_NOT_SUPPORTED` 或 `CONTENT_FORM_NOT_SUPPORTED`。

### 引擎流水线

```text
Brief (创意输入)
    ↓
ModeRegistry.resolve() → ModeProfile + Capability 集合
    ↓
Template Resolver → StoryTemplate
    ↓
Graph.run_pipeline():
    ├── Project Node (立项)
    ├── Series Plan Node (系列规划)
    ├── Parallel Episode Planning (并行逐集规划)
    ├── Episode Writer (逐集生成)
    ├── Validators (Tier-1 确定性 + Tier-2 语义裁判 + 证据评审)
    └── Repair Loop (修复路由，预算约束)
    ↓
ContentFormRegistry.resolve() → Handler.writer() / .package()
    ↓
Final Output (dict with form_key + prose fallback)
```

### Long-form Async Runtime (Phase 7)

```text
submit_long_form() → job_id
    ↓
LongFormWorker.run():
    ├── _fresh_long_form() 或 _resume_long_form()
    ├── 逐章执行 execute_chapter()
    ├── 每章后 Checkpoint 持久化
    └── 最终 StoryRecord 持久化
    ↓
cancel_job() → 协作取消
resume_job() → 从 checkpoint 恢复
```

**持久化链路**：`PostgresCheckpointRepository` + `PostgresLongFormJobRepository` + `PostgresStoryRecordRepository`，支持 SQLite（开发/测试）和 PostgreSQL（生产）。

### Character Memory (Phase 8)

```text
Story COMPLETED
    ↓
CharacterMemoryService.capture()
    ├── 提取关键事件
    ├── MemorySelector.apply_budget() (top_n + max_chars 双上限)
    ├── MemoryCanonGuard.validate() (不覆盖 Canon)
    └── CharacterMemory 持久化
```

---

## 测试体系

| Phase | 测试文件 | 覆盖范围 |
|-------|----------|----------|
| 1 | `test_phase1.py` | Fail-Fast / Deadline / Output Guard |
| 2 | `test_phase2_mode.py` | Viral Drama Mode + Engine |
| 3 | `test_phase3_character.py` | Character Canon + Runtime |
| 5 | `test_phase5_mode_template.py` | General Mode + Template Resolver |
| 6 | `test_phase6_*.py` (×6) | Content Form / Routing / Guard / Runtime |
| 7 | `test_phase7_*.py` (×6) | Async Job / Persistence / Restart / Cancel / Long-form |
| 8 | `test_phase8_character_memory.py` | Memory / Selector / Guard / Summarizer |
| 9 | `test_phase9_*.py` (×4) | Mode Registry / Form Registry / Validators / Persistent Resume |

**总计：428 tests / 0 failures / 0 skipped**

---

## 持久化配置

```python
# 环境变量
PERSISTENCE_BACKEND=memory   # 内存模式（默认，无外部依赖）
PERSISTENCE_BACKEND=postgres # PostgreSQL 模式
# DATABASE_URL通过安全环境注入，不在文件内提供数据库密码

# Docker PostgreSQL
docker-compose up -d
```

此节为旧引擎配置说明。当前HTTP服务要求PostgreSQL，已完成PG16集成与恢复验收；请使用[交付部署手册](docs/handoff/09-deployment.md)中的STORY_DATABASE_URL配置，不能用进程内模式承载正式业务。

---

## 数据库迁移

```bash
cd 故事生成0.2.1
alembic upgrade head    # 应用全部迁移
alembic history          # 查看迁移链

# 迁移版本
# 001: Phase 4 baseline (stories, templates, characters, jobs, traces)
# 002: Phase 8 character_memory (character_memories 表)
```

---

## 设计约束

1. **校验是节点，不是提示词。** 每条规则编译为可执行判定，不写进 prompt "请确保..."
2. **校验门放在能修它的那一步之后。** 不做末端统一校验
3. **修复分类路由，确定性优先。** 零成本机械修复优先，语义判断才调用模型
4. **模型声明，引擎核对。** 让模型写结构化字段，引擎做差分判定

---

## 待完成项

| 项目 | 状态 | 说明 |
|------|------|------|
| Interactive Mode | DEFERRED | 需 interrupt/resume/replanning 语义 |
| pgvector / Semantic Memory | DEFERRED | 语义相似度检索 |
| Physical PostgreSQL Validation | PENDING | 当前 SQLite 已验证持久化语义 |
| Real Provider Creative Acceptance | PENDING | 429 RATE_LIMIT |
| Frontend / TTS | DEFERRED | 后续 Phase |

---

## 分支策略

```text
main                          ← 稳定分支
phase9-final-acceptance-closure  ← 当前开发分支 (HEAD: dba678a)
phase9-more-mode-form         ← Phase 9 基础实现
phase8-character-memory       ← Phase 8
...
```

---

## 提交历史（近 5 次）

```text
dba678a Phase 9: finalize production resume state and standup performer evidence
35daafe Phase 9: finalize SQL-backed mode resume and form structure evidence
c6af30a Phase 9: close structural validators and persistent mode resume acceptance
d9907fc Phase 9: new modes (mystery, serialized) + content form registry + 4 new forms + 42 tests
6527076 Phase 8: close memory budget and Chinese keyword acceptance evidence
```
