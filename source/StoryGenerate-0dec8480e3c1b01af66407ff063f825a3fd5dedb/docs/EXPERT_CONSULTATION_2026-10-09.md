# StoryGenerate 专家会诊材料（2026-10-09）

## 1. 会诊目标

项目正在从“输入创意直接生成”升级为可审计的 AI 玩具广播剧工作流：

```text
创作请求 → 结构化 Plan → 用户修订／确认 → 审批快照
→ 真实模型生成 → 逐句演绎标注 → 质量门 → 不可变版本 → 可播放
```

本次希望专家重点判断：领域状态机与事务边界是否可靠、生成质量门是否合理、
模型调用的可靠性与成本如何治理，以及当前实现离可发布系统还有哪些关键缺口。

代码评审基线：分支 `codex/story-workflow`。本文随分支提交，最终评审应以
GitHub 分支最新提交为准，不以旧 `phase9-final-acceptance-closure` 为准。

## 2. 当前已经完成

### 2.1 工程与旧主链修复

- 根目录 `pyproject.toml`，区分运行、数据库和测试依赖。
- 统一正式内容形式为 `audio_drama`；保留旧入口兼容。
- Long-form Worker／Resume 保存原始 Brief 和真实 `story_mode`，不再以
  `request_id` 代替故事输入。
- Worker 严格完成门：失败结果不能写为 completed。

### 2.2 新工作流合同与状态机

- `create / continue / revise / remix` 判别联合请求 Schema。
- 框架无关的 `StoryWorkflowService`，覆盖 Plan 准备、修订、审批、生成、
  查询、取消、来源解析、角色 Profile 更新和播放事件记录。
- Plan、Job、StoryVersion 独立状态；旧 revision、角色版本漂移、来源漂移、
  幂等键冲突均有确定性错误。
- 正式生成只读取持久化的 `ApprovedPlanSnapshot`，不能绕过审批直接塞 idea。
- Continue／Revise／Remix 已有领域行为和自动化测试，但尚未完成真实模型矩阵验收。

### 2.3 PostgreSQL 与版本谱系

- Alembic `003_story_workflow` 增量迁移，当前数据库为 `003 (head)`。
- `story_plans`、Plan revisions、Story versions、角色 Profile revisions、
  Playback events，并扩展 `story_jobs`。
- 审批事务使用行锁、revision／指纹／角色版本校验和唯一幂等约束。
- StoryVersion 保存完整 JSON 交付；PostgreSQL 触发器限制正文与来源身份更新。
- 2026-10-09：Docker PostgreSQL 16 健康；重复 `upgrade head` 成功；集成测试 2/2 通过，
  覆盖并发审批幂等、重启读取和模板快照持久化。

### 2.4 真实模型 Create 纵向闭环

- 真实模型产生 Plan，支持反馈后生成新 revision。
- 审批后冻结偏好、角色、来源、规则版本、模型映射和模板快照。
- 真实模型生成正文、演绎提示与质量判断，不在正式验收中回落 Mock。
- `StoryDelivery → EpisodeDelivery → SceneDelivery → Utterance` 稳定 JSON 合同。
- 发声行要求 emotion、tone、performer 和至少一个可解析的 Unicode 重音区间。
- 新增 `preview_blurb`：播放前 1–2 句无剧透简介；必须为 15–160 字的单段文字。
- 正文对白角色必须来自获批角色；目标时长门收紧为 85%–115%。
- Prompt 版本当前为 `p1.3.0`；修改 Prompt 会进入规则／缓存指纹。

### 2.5 模板录入

- 版本化 `StoryTemplateSpec` JSON、只读校验／PostgreSQL 导入 CLI。
- 显式 `template_id + revision` 会解析并冻结进 Plan、指纹和 Job。
- 同版本禁止覆盖；模板格式与操作见 `docs/TEMPLATE_AUTHORING.md`。
- 已提供完整短篇和长篇开场两份谍战悬疑模板样例。

### 2.6 演示内容

- `6-实测产物/谍战悬疑_五分钟双版本.md`：完整五分钟短篇与长篇前五分钟。
- 两版初稿调用真实 `qwen/qwen3.7-plus`，JSON 中保留模型、token 和延迟证据。
- Markdown 台本经过人工重写证据链和节奏，不是模型原样输出，不能冒称正式
  `READY` StoryVersion。

## 3. 当前验证结果

| 验证项 | 当前结果 | 说明 |
| --- | --- | --- |
| 单元测试 | 478/478 通过 | 当前 Prompt `p1.3.0`；2026-10-09 全量复核 |
| PostgreSQL 迁移 | 通过 | `003 (head)`，重复升级成功 |
| PostgreSQL 集成 | 2/2 通过 | 并发审批／幂等／重启、模板快照 |
| 模型 Tier 探测 | 曾完成 5 档结构化探测 | 既定 Fast 名称不可用，显式改为 `qwen/qwen3.6-flash` |
| 旧真实 Create 矩阵 | 7/12 自动 READY | Git `8b3beec`；使用旧质量门，不能作为当前发布结果 |
| 当前真实 Create 矩阵 | 未通过／未完成 | 详见下一节；当前 SHA 尚无 12/12 证据 |
| 人工评分 | 未执行 | 目标至少 10/12 四项达到 4/5 |

## 4. 真实模型验收事实

### 4.1 旧批次 `8b3beec`

- 12 例中 7 例自动标为 READY。
- 4 例读取超时，1 例 HTTP 503。
- 复盘发现一个 300 秒样本仅估算 206 秒，并出现未获批 `role-5`；说明旧质量门
  存在假阳性。因此该批次保留作诊断证据，不作为发布通过。

### 4.2 新质量门后的批次

- `4f97807`：第 1 例三次生成后仍为 147 秒，超出 120 秒目标的 85%–115%；
  第 2 例模型读取超时。批次随后主动停止。
- 为正文节点增加精确字数预算：120 秒约 420 字，允许 357–483；300 秒约
  1050 字，允许 893–1207；反馈会告诉模型实测结果与目标范围。正文节点超时
  上限调整为 240 秒，Prompt 版本提升至 `p1.3.0`。
- `3aa483d` 单例复测遭遇 TLS `UNEXPECTED_EOF_WHILE_READING`，属于外部模型链路失败。
- 当前代码没有用 Mock、旧结果或人工编辑稿替代失败样本。

失败 JSON 位于：

- `6-实测产物/phase10_acceptance_4f97807_20261009/`
- `6-实测产物/phase10_acceptance_3aa483d_20261009/`

## 5. 已知问题与风险

### P0：发布阻塞

1. **当前 SHA 尚未达到真实 Create 12/12。** 网关出现读取超时、503、TLS EOF；
   模型也可能连续三次不服从时长范围。
2. **时长仍是文本启发式，不是音频实测。** 当前按 3.5 Unicode 字符／秒估算，
   未结合具体 TTS、标点停顿、语速、音效占时。建议专家确定发布口径：文本预算、
   TTS dry-run，还是合成音频实测。
3. **真实模型验收吞吐较低。** 单个 300 秒样本可能包含 Plan、最多三次正文、
   多场演绎标注与 Judge，曾耗时约 13 分钟；12 例并发又会加重网关波动。

### P1：进入生产前必须补强

1. **可靠性策略不足。** 需要决定调用级指数退避／抖动、Job 级安全重试、熔断、
   超时预算、明确 Strong Alt 降级条件，以及重试时如何保持审批快照不漂移。
2. **质量 Judge 仍由模型承担。** Schema、重音、角色、时长是确定性门；大纲对齐、
   连续性和分级依赖模型判断。需评估对抗提示、误判率和双 Judge／规则复核。
3. **PostgreSQL 验收覆盖不完整。** 当前自动集成测试仅 2 项。发布矩阵仍要补迁移
   升降级、失败回滚、临时断连、StoryVersion 触发器、并发角色变更等独立用例。
4. **四操作真实验收不完整。** Continue／Revise／Remix 主要是合同和替身测试，
   尚无与 Create 等量的真实模型、连续性和版本谱系证据。
5. **空角色请求的产品语义需定稿。** 没有预选硬件角色时，是允许模型创建临时
   叙事角色，还是必须先生成并审批角色快照；目前需要专家统一 Canon／performer 边界。

### P2：产品化缺口

1. 没有 FastAPI／前端、模板管理页面、审批 UI、Job 进度推送。
2. 没有 TTS Provider、播放适配、边生成边播放或硬件动作控制。
3. 没有旧 `story_records` 的显式导入工具；旧内容不会自动标 READY。
4. Prompt 分布在正式 workflow、旧 Graph、Judge 和结构化输出公共指令中；已有
   `docs/PROMPT_MAINTENANCE.md` 路由说明，但尚未建设统一 Prompt Registry／版本仓库。
5. API key 未写入仓库；鉴于密钥曾在会话中明文提供，面向更多专家共享环境前建议轮换。

## 6. 建议专家重点会诊的问题

1. 85%–115% 的文本时长门是否合理？是否应按 dialogue／narration、`speech_rate`、
   pauses 和 SFX 分别计时？
2. 生成器应继续“整集最多重写三次”，还是改成扩写／压缩的局部编辑器？后者如何
   保证节拍、连续性与重音索引同步更新？
3. 面对超时／503／TLS EOF，哪些调用可以自动重试，哪些必须失败 Job？是否允许
   同 Tier 显式切换 Strong Alt，审计字段如何表达？
4. 大纲一致性、连续性和安全 Judge 是否需要不同模型、不同提示词或确定性证据表？
5. ApprovedPlanSnapshot 中还应冻结哪些内容：Prompt 全文哈希、Schema 版本、模板完整
   JSON、规则库哈希、Provider 参数、采样种子？
6. Continue 的 `last_played` 与“创作下一集”如何在产品入口彻底区分，避免误生成？
7. Revise 的影响分析如何从当前启发式升级为事实／关系／伏笔依赖图？
8. 空角色请求、叙事 NPC、硬件角色和 performer 的约束应如何分层？
9. StoryVersion 不可变范围是否应包含质量报告和模型 Trace？状态迁移是否允许单独更新？
10. 首次发布是否缩小到 Create + 单集 120／180 秒，暂缓 300 秒和其余三种操作？

## 7. 建议后续执行顺序

1. 专家确认时长口径、模型重试／降级政策和角色边界。
2. 将上述结论落实为确定性合同与负向测试。
3. 补全 PostgreSQL 发布矩阵；在 CI 使用独立临时库。
4. 逐 Tier 做最小结构化探测，再以串行或受控并发重跑同一 Git SHA 的 12 例。
5. 12/12 自动门通过后，完成至少 10/12 的人工四项评分。
6. 固化发布证据清单，再决定是否建设 HTTP／前端和 TTS 适配。

## 8. 复现命令

```powershell
python -m pip install -e '.[dev]'
docker compose up -d postgres
$env:DATABASE_URL = 'postgresql+psycopg://storygen:storygen_dev@localhost:5432/storygenerate'
python -m alembic upgrade head
python -m pytest tests/unit -q -p no:cacheprovider --basetemp=.test-tmp
$env:TEST_DATABASE_URL = $env:DATABASE_URL
python -m pytest tests/integration/test_postgres_workflow.py -q -p no:cacheprovider --basetemp=.test-tmp
```

真实验收需在进程环境注入 `DRAMA_LLM_API_KEY`，不得写入命令历史、文档或仓库：

```powershell
$env:STORY_LLM_FAST_MODEL = 'qwen/qwen3.6-flash'
$env:STORY_ACCEPTANCE_MAX_WORKERS = '1'
$env:STORY_ACCEPTANCE_OUTPUT_DIR = '<独立证据目录>'
python tests/acceptance/probe_models.py
python tests/acceptance/real_create_matrix.py
```

## 9. 关键阅读入口

- 总览：`README.md`
- 工作流交接：`docs/WORKFLOW_HANDOFF_PHASE10.md`
- Prompt 维护：`docs/PROMPT_MAINTENANCE.md`
- 模板录入：`docs/TEMPLATE_AUTHORING.md`
- 领域 Schema：`4-引擎代码/drama_engine/workflow/schemas.py`
- 应用服务：`4-引擎代码/drama_engine/workflow/service.py`
- LLM 执行与演绎：`4-引擎代码/drama_engine/workflow/adapters.py`
- 持久化：`4-引擎代码/drama_engine/workflow/repository.py`
- 质量门：`4-引擎代码/drama_engine/workflow/performance.py`
- 迁移：`alembic/versions/003_story_workflow.py`
- 真实验收：`tests/acceptance/real_create_matrix.py`
