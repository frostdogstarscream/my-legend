# 获批大纲故事工作流：实施与验收交接

## 当前实现

新入口为 `drama_engine.workflow.StoryWorkflowService`，输入使用 `intent` 判别的
`CreateStoryRequest / ContinueStoryRequest / ReviseStoryRequest / RemixStoryRequest`。
正式执行器为 `LLMPlanGenerator` 与 `ApprovedPlanLLMExecutor`。执行器只接受持久化
`GenerationJob.input_snapshot` 中的 `ApprovedPlanSnapshot`。旧 `run_pipeline` 保留兼容。

Create 流程：`prepare_story_plan` →（可选）`revise_story_plan` →
`approve_story_plan` → `generate_from_approved_plan`。审批对 Plan revision、指纹、
角色 Profile revision、动态来源版本和幂等键执行校验。事务由应用服务提交；
传入的 SQLAlchemy Session 不应同时承载其它未提交写入。

首发 JSON 合同只允许 `audio_drama`、默认 `zh-CN`，产品时长为
120/180/300 秒，90/150 秒保留兼容。14–17 仅可选 `teen`。
`mature_non_explicit` 须 18+，禁止露骨色情与极端血腥。

正文交付含 `StoryDelivery → EpisodeDelivery → SceneDelivery → Utterance`。
新交付增加 `preview_blurb`，供播放前展示 1–2 句无剧透简介；
`summary` 继续承担剧情概括与连续性用途，二者不混用。缺少简介会阻止新交付通过质量门。
发声行必须有有效 `emotion`、`tone_instruction`、至少一个重音索引以及
`performer_id`。重音 `[start_char, end_char)` 按 Unicode 码点由确定性代码解析。
模型注释失败时最多定向修复两次；正文只进行一次首次创作，若唯一错误是时长越界，
最多替换一个场景一次，不再全文循环重写。
还需通过集数、Plan 快照、连续性、大纲与内容分级质量门，才可提交 READY 版本。

Continue 区分 `next_episode` 与 `resume_playback`；后者不创建故事 Plan，
通过 `resolve_story_reference` 定位已播放版本和位置。Revise 提供受影响集号分析，
Remix 产生新 `story_id` 并保留父版本谱系。旧 StoryRecord 不自动转为 READY。

## 安装与运行

```powershell
python -m pip install -e '.[dev]'
$env:DATABASE_URL = 'postgresql+psycopg://<user>:<password>@<host>:5432/<db>'
python -m alembic upgrade head
$env:DRAMA_LLM_API_KEY = '<运行时注入真实密钥>'
$env:STORY_LLM_FAST_MODEL = 'qwen/qwen3.6-flash'
python tests/acceptance/probe_models.py
python tests/acceptance/real_create_matrix.py
```

2026-10-08 网关探测：既定 Fast 名称 `deepseek-v4-flash` 返回 404，
`deepseek-v4.1-flash` 返回 402。上述 `qwen/qwen3.6-flash` 是明确、已探测可用的
替代配置，不触发隐式 Mock 或 Strong 自动故障降级。其余 Tier 仍使用既定模型。

```powershell
python -m pytest tests/unit -q -p no:cacheprovider --basetemp=.test-tmp
$env:TEST_DATABASE_URL = $env:DATABASE_URL
python -m pytest tests/integration/test_postgres_workflow.py -q -p no:cacheprovider --basetemp=.test-tmp
```

## 数据库与回滚

`001` 现在可在空库创建旧基线表，原 `001→002` 历史顺序不变；
`003_story_workflow` 增量创建 Plan、Revision、StoryVersion、Profile Revision、
PlaybackEvent 表并扩展 `story_jobs`。PostgreSQL 触发器禁止修改已保存的
StoryVersion 正文与来源身份。正式环境先备份，再运行 `alembic upgrade head`。

需要回退新表时，先停止新工作流写入并备份新表，再执行
`python -m alembic downgrade 002`。此操作会删除 003 创建的表及数据，
不能作为无备份回滚；旧 `story_records` 不受 003 回退影响。

## 验收口径

单元测试使用测试替身，只验证合同、状态机和失败门；真实验收脚本只创建
`OpenAICompatProvider`，没有 Mock 回落。12 个固定样本覆盖三档用户、
三档时长、四种 Mode、角色数量与两档分级，其中 4 个先修订 Plan。
可设置 `STORY_ACCEPTANCE_OUTPUT_DIR` 保存每例 Plan、故事正文与模型调用证据，
供人工按大纲一致性、连贯性、自然度和演绎提示自然度四项评分。

人工评分、硬件/TTS 适配、Fast 既定模型恢复、以及旧内容显式导入工具仍属于
后续发布工作。当前不将仅凭自动校验通过的故事等同于人工发布验收完成。

## 2026-10-09 真实验收批次复盘

在 `8b3beec` 上运行 12 个固定样本：7 例生成并持久化为 `READY`；
第 06、08、09、12 例因网关读超时失败，第 11 例因 HTTP 503 失败。
这些是未通过样本，不能以 Mock 或旧运行结果补算为 12/12。
原始 Plan、Delivery、逐节点模型调用和错误证据保存在
`6-实测产物/phase10_acceptance_8b3beec_20261008/`。

本批次也暴露机器质量门仍需收紧：第 03 例声明目标 300 秒，正文按现有
3.5 字/秒算法仅估算 206 秒，但当前下限 65% 仍允许其 READY；正文还出现
`role-5`，而批准角色只有 4 个。此例可作故事原案，不能原样作为五分钟
发布脚本。`6-实测产物/5分钟演示广播剧脚本.md` 是据此重新编辑的
演示稿，不冒称原始 READY 版本的逐字内容。随后已将正文时长要求收紧为
目标的 85%–115%，并在演绎标注前拒绝未审批的对白角色；新增负向测试，
全量 471 项单元测试通过。这些代码修改尚未完成同一新 SHA 的 12 例
真实模型复验，不能沿用本批次的 7/12 作为新代码发布结果。

## 模板录入与新版工作流

模板采用版本化 `StoryTemplateSpec` JSON；格式、校验／导入命令和示例见
`docs/TEMPLATE_AUTHORING.md`。`template_ref` 必须包含 ID 和 revision。
新版工作流现在要求显式提供 `StoryTemplateResolver`，将解析后的模板快照
写入 Plan、指纹和审批 Job；没有 Resolver 时显式模板请求报错，不再被忽略。
模板数据库仓库使用 `StoryTemplateModel` 而非直接查询 Pydantic 模型。
新增持久化快照和字数预算回归测试后，全量单元测试达到 478 项。2026-10-09
Docker PostgreSQL 16 恢复并保持 healthy，数据库为 `003 (head)`；重复迁移成功，
PostgreSQL 集成测试 2/2 通过，覆盖并发审批／幂等／重启和模板快照。
完整发布数据库矩阵仍未执行，详见 `docs/EXPERT_CONSULTATION_2026-10-09.md`。

随后真实批次在 `4f97807` 暴露 120 秒正文生成成 147 秒及模型读取超时；
`3aa483d` 已给正文节点加入精确目标字数范围并把 Prompt 版本升级至 `p1.3.0`，
但单例复测遭遇 TLS EOF。当前新 SHA 没有 12/12 READY 证据，不能发布。
