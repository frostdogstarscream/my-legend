# StoryGenerate 创作优先优化实施方案

日期：2026-10-09
开发分支：`codex/creation-first-token-accounting`

## 1. 推荐路线

正式主链保持不变：请求 → Plan → 用户确认 → Approved snapshot → 正文 → 演绎 → 必要门禁 → READY。
优化只发生在获批快照之后的上下文装配和节点职责内，不绕过审批、不重写 Phase 10 状态机。

```text
ApprovedPlanSnapshot
  └─ 确定性 EpisodeCreativePacket（0 次模型调用）
       ├─ 人物目标/知情范围/说话习惯
       ├─ 连续性事实/未解线程
       ├─ scene cards：任务、beats、状态变化、逐场字数
       └─ 文字/音效/停顿时间预算
            ↓
       首次 DraftEpisode（1 次正式创作）
            ↓
       硬约束校验
       ├─ 通过：演绎标注
       ├─ 仅时长失败：最多一次、仅替换一个场景
       └─ 角色/事实等失败：明确失败，不全文重写
```

## 2. 本轮已经实施

### P0 创作质量

- 新增 `workflow/creative_context.py`：把审批快照整理为确定性创作包，不增加模型调用。
- p1.5.0 正文 Prompt 明确使用 scene cards、人物知情边界和声音时间预算。
- 正文从“最多三次完整生成”改为“一次首稿 + 纯时长错误最多一次单场替换”。
- 场景替换只返回一个 `DraftScenePatch`，不会改简介、结局、事实增量或其他场景。
- 演绎标注改用独立 `performance_annotation` Fast Tier；正文继续 Balanced，Plan 继续 Strong。
- 线上 Judge 仍只检查大纲、连续性、分级，不新增文学评分和自动全文重写。

### P1 用量与成本基础

- 新增 request-scoped usage context，贯穿 request、plan、job、stage、node 和 attempt kind。
- OpenAI Compatible Provider 的每个真实 HTTP attempt 记录 call ID、实际模型、缓存/推理 Token、
  Usage 来源、状态、错误、时延和起止时间。
- 供应商没返回 Usage 时标记 `estimated`；超时或无法获知时标记 `unavailable`。
- 新增 Decimal 成本原语，分开 confirmed、estimated 和 unknown，不重复计算已包含在 output 中的
  reasoning tokens。
- 真实 Create 脚本新增首稿是否直接通过、局部修复次数、总调用数及 reported Token 汇总；失败案例
  同样保留调用 attempt。

## 3. 推荐模型分工

| 节点 | 推荐 Tier | 原因 | 成本控制 |
|---|---|---|---|
| Plan/Plan revision | Strong | 多约束因果、角色目标、长线悬念需要较强推理 | 用户主动修订单独归因，不算系统失败重试 |
| Episode writer | Balanced | 中文对白、完整结构和长 JSON 的综合要求 | 争取一次成稿；仅硬时长允许一次局部修复 |
| Performance annotation | Fast | 文本固定后的分类、短指令和原文 span | 保持确定性索引校验，失败只修缺失行 |
| Delivery judge | Fast | 三项明确布尔核验 | 单次调用，不承担文学评分或重写 |
| 离线文学盲评 | 人工/独立实验 | 避免线上成本和模型自评偏差 | 仅版本评审运行 |

模型选择必须由同条件 A/B 证明。更贵模型不自动等于更好；便宜模型若造成重试增加，也可能提高总成本。

## 4. 分阶段验收

### 阶段 A：工程回归

1. 全量 unit tests。
2. PostgreSQL 003 重复迁移与 integration tests。
3. 核对 p1.5.0、规则哈希和审批快照。
4. 验证 provider 的 reported/estimated/unavailable 和失败 attempt。

### 阶段 B：4 例小规模真实模型

- 温情/成长 120 秒；
- 轻悬疑 120 秒；
- 日常/友情 180 秒；
- 悬疑/冒险 180 秒。

实验 A 固定 Approved Plan，只替换正文 Prompt；实验 B 从相同请求生成 Plan 到 Delivery。保存未经人工编辑的
JSON、Git SHA、Prompt 版本、模型、采样参数、每次调用、Token、时延和失败类型。

### 阶段 C：12 例与四操作

小样本确认网关和首稿命中率后，再运行固定 12 例。Create 稳定后，按 Continue、Revise、Remix 顺序补真实
连续性和版本谱系证据。不得用历史结果、Mock 或人工稿补齐失败样本。

## 5. 成功指标

- `first_draft_contract_passed`：正文第一次调用即满足 Schema、角色和时长。
- `first_delivery_passed`：第一次正文与第一轮演绎后即可 READY。
- 网络失败率、内容硬约束失败率、文学不接受率分别统计。
- 每任务 reported/estimated/unknown Token、调用次数、P50/P95 时延。
- 离线盲评：因果、人物辨识度、对白/声音自然度、悬念/结局兑现、继续收听意愿。

## 6. 风险与后续任务

- `major_beats` 仍是字符串。待 p1.5.0 实验确认后，再评估兼容新增结构化 `BeatCard`，避免一次改动混入
  Prompt、Schema 和工作流三种变量。
- 单场修复可能无法纠正极端长度偏差；此时明确失败比全文静默重写更可审计。
- 3.5 字/秒仍是文本估算，真实发布时长需要 TTS dry-run 或成品音频测量。
- Prompt/Schema 哈希尚未独立冻结到 Approved snapshot；当前由 rule version/PROMPT_VERSION 参与指纹，
  跨部署排队任务仍应增加执行时哈希比对。
- Token 账本数据库迁移本轮只形成设计，不与 P0 同时扩大改动面。

## 7. 回滚

本轮没有数据库迁移。回滚时恢复 p1.4.1、移除创作包输入和单场修复器、将 performance route 恢复为旧路由即可；
Phase 10 的 Plan、Job、StoryVersion 数据不受影响。已经用 p1.5.0 创建的 Plan 应按旧版本 Plan 处理为 stale，
不可跨 Prompt 版本继续执行。

## 8. 本轮验证结果

- 全量单元测试：`489 passed in 314.60s`。
- PostgreSQL 16：容器 healthy，数据库 `003 (head)`，重复 `upgrade head` 成功。
- PostgreSQL 集成：`2 passed in 7.19s`。
- 五档真实模型最小探测：5/5 成功。
- 正式真实 Create 小样本：2/2 READY、2/2 首次正文硬约束通过、0 次正文局部修复；文学质量仍未放行。
