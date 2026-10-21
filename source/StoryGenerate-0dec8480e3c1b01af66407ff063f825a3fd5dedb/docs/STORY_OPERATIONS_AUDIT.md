# 四种故事操作审计与执行合同

基线：`codex/story-formula-integration`，`712247446a246b09ba35ca9816c9230d1e1ccc99`。范围为引擎与结构化台本，不包括真实 TTS 时长、HTTP 服务或玩具调度。

## 统一调用链及事务

`StoryOperationRequest` 按 intent 区分四种请求 → `StoryWorkflowService.prepare_story_plan` → 来源 READY 校验、参数/角色继承、修改目标验证 → Strategy/Template 解析 → `LLMPlanGenerator.generate` → `PlanPreview` 与指纹 → `create_plan` 提交 → `approve_story_plan` 校验 revision/指纹/角色资料/动态来源 → 同事务保存审批快照及 QUEUED Job → `generate_from_approved_plan` 原子领取 → `ApprovedPlanLLMExecutor.execute` 正文/局部替换及真实演绎 → 现有 Judge 加操作要求逐项证据 → 完整性/范围/快照质量门 → StoryVersion 与 SUCCEEDED Job 同事务提交 → 新 Session 重读 → `export_delivery_bundle`。

Plan、Revision、Job、StoryVersion 分别存于 `story_plans`、`story_plan_revisions`、`story_jobs`、`story_versions`。Job JSON 冻结 request、preview、模型路由；Delivery JSON 保存完整台本、来源、连续性及模型调用记录。角色资料与播放记录使用既有版本表。应用服务管理事务，调用者不要在同一 Session 混入其他业务写入。

| 操作 | 请求特有字段 | 输出与版本关系 |
| --- | --- | --- |
| Create | 无来源；可选 characters/template_ref | 新 story_id、version_no=1、无父版本 |
| Revise | source_selector、edit | 同 story_id、新版本、父版本为准确来源；返回完整故事 |
| Continue | source_selector、next_episode | target_episodes 为新增集数；同 story_id、新版本、全量剧集按全局序号排列 |
| Remix | source_selector、remix | 独立 story_id、version_no=1，保留来源 parent_story_version_id |

同故事版本号在保存事务内分配 max+1，以 PostgreSQL advisory transaction lock 串行保护。版本号表示提交顺序，父版本表示实际派生关系，历史版本可以派生多个子版本。`003` 迁移的触发器禁止修改已保存正文和来源身份。

## 修改与续写合同

- line：target 必须含 episode_id、scene_id、line_id；只替换该行，kind 与发声身份不变。
- scene：必须含 episode_id、scene_id；替换该场台词，保留场景身份和其余场景。
- episode：必须含 episode_id；只重写该集。
- arc：必须含按播放顺序排列的连续 episode_ids，不再把任意 arc 当作全故事。
- ending：可省略 episode_id（最后一集），scene_ids 必须为该集连续尾部场景，省略时使用最后一场。
- whole_story：修改整个来源版本。所有范围均返回完整故事，原版本不变。
- branch_from_here 仅修改目标；replan_affected 将目标之后的剧集纳入审批快照并重写，目标集内非目标场景仍保护。

`generated_episode_ids` 为可缺省的新字段；旧 JSON 可读取，新任务交付必须填写本次新增/替换的剧集 ID。审批 Plan 只包含本次生成范围，Continue 的 episode_outlines.index 使用全局集号，Revise 使用受影响集号；Delivery.episodes 始终是完整播放顺序。不把完整剧集数量错误地与 Continue 新增集数比较。

Continue/Revise 继承来源审批参数，明确提供的参数覆盖继承值，target_episodes 在 Revise 中由实际修改范围确定。来源剧情名、角色身份和演绎者保持不变。每集生成后折叠真实增量再生成下一集；Revise 根据保留/替换后的完整剧集重建状态；Remix 从二创结果重建状态，不注入被改变的旧事实。事实/关系有稳定 id/fact_id/relationship_id 时新值替换旧值；无稳定 ID 的条目保留时间顺序，不猜测对象语义。closed_threads 必须使用已有线程原文。

新增字段 `quality_report.operation_checks` 保存 requirement_id、判定及正文逐字引文；漏项、失败项或虚构引文均阻止发布。模型语义判定仍需验收报告逐项复核，不能将自动 READY 等同于产品验收。

`client_context.operation_requirements` 可提供额外的命名验收条件（字符串键→条件文本），与操作固有要求一起进入规划、正文和 Judge。Judge 的 requirement_id 枚举、数量和完整覆盖由动态 Schema 约束，拒绝模型自行编造 ID。evidence_line_ids 使用当前交付的真实行 ID，代码提取原文；已带行号的引文仅在剥离明确编号包装后仍逐字匹配对应原文时归一化，保留 raw_evidence_quotes，不接受改写引文。closed_threads 只能引用已知未解线程的原名，不得附加解释。Plan 的集数与时长同样绑定请求 Schema，防止局部修改误返回整部大纲。沿用 Provider 的一次 JSON 合同修复，不增加正文评价工作流。局部变化行按场景批量演绎，未变化行复用原标注。提示词版本为 p1.6.11。

`client_context.evidence_kinds` 可约束指定要求的可接受证据种类。铃声真正发生等可听事件可要求 sfx/action/narration，避免只凭“我准备敲/我敲了”台词或只举铃的 cue 宣布回收。多集 closed 结局只校验最后一集的尾部，非最后一集合理留下下一集任务不会被误拒。

操作语义核验仍是一个已有质量门，使用 Strong 路由 `storycraft_operation_judge`，旧 Graph 的 judge 路由保持原样。同一核验返回 `timeline_claims`（event_id、kind、normalized_time、line_id、quote）；代码检查中文/冒号时钟原文定位、提取覆盖、同一事件安排/实际时间及明确改期，错误阻止 READY。模型原判保留在 model_passed，代码判定另列 deterministic_errors。写作与规划同时限制未依据输入随意添加精确时刻。

该规则不等于通用日历或完整时间语义证明；相对时间、跨日、事件归一化仍依赖模型和产物复核。真实反例表明单纯换 Strong 仍会漏判，结构化证据缺失/未落地的候选必须失败，不能用模型布尔值补算成功。

## 异常与兼容

Job 的 execution_token 是 JSON 中的运行令牌，无新增数据库列。领取必须从 QUEUED 原子转换；重复成功执行返回持久化结果；取消与发布竞争通过行锁协调。显式 `recover_interrupted_job(job_id, expected_execution_token)` 使旧 worker 失去发布资格，标记 WORKER_INTERRUPTED；`retry_failed_job(job_id)` 重新排队。不存在后台自动判断 worker 死亡或自动恢复调度器。

失败不保存 READY StoryVersion，重试复用冻结的审批快照。验收入口另存候选和原始模型响应，包括未形成完整候选的失败。旧 GraphStoryExecutor 只保留 Create 兼容入口，对另外三种操作明确拒绝，防止走旧链误报成功；四项产品操作使用 ApprovedPlanLLMExecutor。

历史固定模板脚本确有两处误判：完整性失败状态被后续 ok 覆盖；verify_round_trip 的二元组被直接当布尔值。历史报告 6/7 和 616 项单测不能作为四操作真实验收结论，必须以新批次证据重新判断。

## 重现验收

在项目目录使用现有 Python 环境，安全注入 DRAMA_LLM_API_KEY；默认数据库连接仅适用于项目 Docker 的开发库。可通过 TEST_DATABASE_URL 指定专用库。

```powershell
docker compose up -d postgres
$env:DRAMA_LLM_API_KEY = [Environment]::GetEnvironmentVariable('DRAMA_LLM_API_KEY', 'User')
python -m pytest tests/unit -q -p no:cacheprovider
$env:TEST_DATABASE_URL = 'postgresql+psycopg://storygen:storygen_dev@localhost:5432/storygenerate'
python -m pytest tests/integration -q -p no:cacheprovider
python tests/acceptance/real_operations_chain.py --rounds 3
```

真实入口仅使用 build_provider_from_env，无 Mock 回退。三轮主链并发执行，每轮按 Create→Revise→Continue→Remix 串行；专项从首轮通过来源派生。每批创建独立 schema 并运行真实迁移，保留 schema 与产物供追溯，不删除验收数据库记录。输出包含 SHA、请求、大纲、审批任务、原始模型调用、候选、正文、重读结果、差异、Token 与时延；失败关联阶段明确阻塞。

Judge 输入另包含代码字面提取的 required_clock_mentions（每行完整原文及所有显式时刻），帮助逐行覆盖重复时刻。该清单不推断事件或判定一致性，未覆盖、虚构及冲突仍走原失败门。

字面提取排除“一点一点/一点一滴”等数量表达，同时保留“凌晨一点一分”的真实时刻。最终业务提交为 `45ac96ab1597a18f50a240c6069068cb79ca76fc`，658项单元与6项PostgreSQL测试通过。三轮主链及修改范围/再次续写共18项最终通过；最终批次20次尝试中2次不合格，第一轮Remix补充同晚calendar约束后重新执行正常审批生成链，原稿没有人工修改。

完整结论、原失败与Token/耗时统计见 [总报告](../6-实测产物/operations_report_20261014/FINAL_REPORT.md)。相对日期仍依赖语义检查和产物复核，不以三轮样本证明任意长故事稳定性。八个保留验收schema最终无待执行任务，历史遗留中断任务均使用显式恢复接口处理；验收记录保留，合并与发布未执行。
