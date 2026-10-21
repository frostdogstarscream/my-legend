# StoryGenerate StoryCraft v2：故事质量诊断与 A/B 验收

> **方向更新（2026-10-09）：** 根据最新业务决策，本 PR 的主目标改为“前置策划与正文 Prompt 强化，争取一次成稿”；**不把文学 Judge、反复重写与评分系统作为主线**。本文保留此前诊断与 A/B 方法供追溯，新的优先级及 Token 统计后续任务以 [CREATION_FIRST_AND_TOKEN_ACCOUNTING.md](CREATION_FIRST_AND_TOKEN_ACCOUNTING.md) 为准。上线端仅保留结构、安全和关键连续性必要核验；人工文学盲评用于离线 Prompt 版本研究。

> 基线：codex/story-workflow @ 86cdeda，候选分支：review/storycraft-prompts-v2-20261009。
> **状态：源码已接入新的创作提示词；尚未运行该分支的真实模型验收。不得将本文件作为发布证明。**

## 1. 诊断结论

故事质量偏弱确实与原正式工作流提示词过短有关，但不只是 Codex 编写水平的问题。真正的缺口有四类：

1. 大纲描述得出“发生了什么”，但没有强制刻画“人物为什么选择、依据是什么、付出了什么代价、后果改变了什么”。
2. 正文创作要求格式、字数、大纲一致，却缺少防止突兀反转、凭空出现的证据、假因果、台词解释剧情的编辑规则。
3. 逐句演绎标注器容易产出连续的“极度震惊／颤抖／大喊”，重音形式上合法但听起来不自然。
4. 质量 Judge 只有大纲一致性、连续性、年龄分级三个布尔判断，没有对白自然度、戏剧行动、悬念有效性等独立质量量表。模型自评不能替代专业盲评。

### 仓库证据

- 旧真实 Create 批次 7/12 READY，但其旧门曾让 300 秒目标、约 206 秒实际估计且出现未审批 role-5 的作品通过。因此 7/12 不是质量证明。
- 草稿 spy_demo_complete_draft.json 与 spy_demo_serialized_draft.json 里的关键推断和剧情反转依赖“突然宣布真相”等捷径。人工改写的谍战悬疑_五分钟双版本.md 修了证据链与节奏，不是模型直接完成的正式 READY 成品。
- 固定 12 例 Create 矩阵的角色资料以“角色1、角色2”及通用目标为主，缺乏角色个性，也是创作质量的输入瓶颈。请将“个性明确的角色卡”纳入**单独**产品质量集，不要擅自改变原来 12 例以免 A/B 失去基线。
- 120 秒稿估算 147 秒、模型读取超时、HTTP 503、TLS EOF 是其他发布问题，不能寄望更长提示词自动修复。

## 2. 本候选分支改动

| 文件 | 用途 |
|---|---|
| 4-引擎代码/drama_engine/workflow/storycraft_prompts.py | 新增 Plan / Episode / Performance / Judge 四份中文编剧级系统提示词 |
| 4-引擎代码/drama_engine/workflow/adapters.py | **真实工作流已改为调用四份新提示词**，不改旧兼容 Graph |
| 4-引擎代码/drama_engine/config.py | PROMPT_VERSION 升为 p1.4.0，使新计划保留不同版本标记 |
| tests/unit/test_storycraft_prompts_v2.py | 无密钥的提示词覆盖范围与执行链连线检查 |

保留原 DraftEpisode、PlanContent、StoryDelivery、QualityDecision 结构；数据库、审批行为、角色不可变规则、时长硬门均未调整。此举可独立检验提示词贡献，不把多个技术修改混进一次试验。

## 3. 四份提示词为什么这样写

**Plan:** 为每集构建“触发事件 → 角色目标 → 受阻 → 付费选择 → 后果”的因果链；major_beats 具体到可听的动作和线索。mystery 公平解谜，serialized 不过早完结长线，viral_drama 有张力但不能套路堆叠；所有 Plan 必须等待用户确认。

**Episode:** 将大纲变为行动中的戏，要求每场发生可验证的状态变化、证据与结论不倒置、没有便利巧合，人物对白反映各自目标和潜台词，而不是轮流朗读设定。字数预算依旧必须遵守，但不得用重复台词和无意义旁白凑时长。

**Performance:** 继续确保每句有情绪、语气与至少一个可验证重音；建议一两处重点、短促可执行的声音指导，不把每句都处理成高强度喊叫。

**Judge:** 从“总体赞美”转为可反驳的证据复核。若认为因果成立，必须能指出前置依据；若失败，reasons 应指明集/场/行、对应错误与修复方向。不改三个布尔输出，避免把文学量化直接冒险接入不可变 StoryVersion。

## 4. 建议真实对照验收步骤

1. 在 codex/story-workflow@86cdeda 与本候选分支分别创建隔离工作树。两组使用相同真实模型、数据、参数和版本化输入。
2. 先各跑 2 个 120 秒案例和 2 个 180 秒案例。确认模型网关稳定后，运行完整 12 例 Create。
3. 同一例保存 Plan、Delivery、Trace、Model ID、Token、延迟、error、Git SHA、Prompt 版本。不能用 Mock 或人工修改稿补上失败案例。
4. **文学质量必须盲评**：至少两位不知模型和分支的评审按因果清晰度、人物辨识度、对白/声音自然度、悬念与结局兑现、整体收听意愿各打 1–5 分。每个低于 4 分的维度指出真实台词和原因。对意见分歧做复核。
5. 结果同时计算 12 例自动门通过率、人工评分、P50/P95 时延、模型调用次数、总 tokens；不要只看某一篇好故事，也不要只看“输出更长”。
6. 另建 4 例有真实人格与说话习惯的角色质量集，两分支使用完全相同角色卡以辨别 Prompt 改变带来的真实效果。

### 建议放行标准（尚未经团队批准）

- 全量单元测试与 PostgreSQL 集成测试无回归。
- 真实模型 12/12 自动门通过、无伪 READY，且网关失败明确记录而非跳过。
- 至少 10/12 故事人工平均质量分达到 4/5，不能出现重大因果漏洞。
- 新版在至少三个创作维度优于基线，其他维度无实质退步。
- 模型成本与完成时间仍处于可接受范围；300 秒故事时间过长时先优化可靠性。

### 本地命令（PowerShell）

~~~powershell
python -m pip install -e '.[dev]'
python -m pytest tests/unit -q -p no:cacheprovider --basetemp=.test-tmp
$env:STORY_LLM_FAST_MODEL = 'qwen/qwen3.6-flash'
$env:STORY_ACCEPTANCE_MAX_WORKERS = '1'
$env:STORY_ACCEPTANCE_OUTPUT_DIR = '.\storycraft_ab_evidence'
# API 密钥使用安全环境变量管理；不要写入脚本、代码仓库或终端历史
python tests/acceptance/real_create_matrix.py 1
python tests/acceptance/real_create_matrix.py 2
python tests/acceptance/real_create_matrix.py 1-12
~~~

PostgreSQL 仍需按 WORKFLOW_HANDOFF_PHASE10.md 在独立测试数据库完成迁移和并发验收。

## 5. 下一阶段不应只靠写 Prompt

**P0：剧情质量**
- 充实角色的具体欲望、关系冲突、回避习惯和选择代价，别长期依赖空泛 Profile。
- 建立证据链检查：让审稿人指出“角色知道这件事”的证据在何处，避免出戏。
- 建立 scene-level 文字预算，避免全文虽然字数满足但重要节点被一笔带过。
- 用 TTS 试读或合成实测校准时长，当前 3.5 字/秒仅是启发式估计。

**P1：成本与可靠性**
- 时长偏差改为目标章节/场景的局部增删，不要每次整集重写三遍。
- 调用级对可重试故障使用有上限的退避；不能把 TLS EOF、503 当作文学生成失败，也不能偷偷换 Mock。
- 约束“多次 JSON 重问 → 3 次正文重试 → 每场重音修补”的组合调用成本。
- 获批 Job 开始执行前检查所用 Prompt Profile/哈希与审批快照一致。单纯改 PROMPT_VERSION 并不能自动防止老 Job 跨部署漂移。

**P2：可持续质量管理**
- 新增 NarrativeCraftScore：剧情因果、人物选择、对白潜台词、有效悬念、情绪与结局兑现，各项必须有文本证据。
- 建立不同模式/受众的困难故事基准以及真正试听数据，不要用单个谍战演示稿判断通用引擎。
- Create 验收稳定后补 Continue / Revise / Remix 的真实模型 A/B 与连续性负向测试。

## 6. 明确的剩余风险

本分支暂未在真实模型或数据库环境重跑测试。更长的提示词可能增加输入 token、生成耗时；Judge 可能更严格导致原本 READY 的故事被拒。这些变化是待验收风险，不是已经解决的问题。

新 Plan 会带 p1.4.0 版本指纹；但仍建议补足**执行时的 ApprovedSnapshot Prompt 版本/内容哈希核验**，防止排队任务跨部署使用新 Prompt。正式上线必须由同一真实 SHA 的通过证据支持，不能拿之前的 478 tests 和 7/12 结果代替。
