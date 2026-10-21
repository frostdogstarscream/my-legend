# Token 使用统计与商业计费设计方案

日期：2026-10-09
范围：本轮完成埋点口径、成本原语和数据库/API 设计；不实现支付、套餐或用户扣费。

## 1. 设计原则

1. 每次实际 HTTP attempt 都是一条账目，包括网络重试、JSON 重问、局部修复和失败尝试。
2. `reported`、`estimated`、`unavailable` 永不混算；未知成本不是零成本。
3. 供应商成本与用户售价分离。前者用于账单对账，后者属于产品定价。
4. 金额使用 Decimal，价格版本不可覆盖，历史账目保存当时的模型与价格快照。
5. reasoning token 若已包含在 completion token 中，不得重复计费。
6. 账目只追加；更正使用 reconciliation 记录，不修改原始调用事实。

## 2. Token 调用账本 Schema

建议新增 `llm_call_ledger`：

| 字段 | 类型/约束 | 说明 |
|---|---|---|
| `ledger_id` | UUID PK | 账目身份 |
| `call_id` | text unique | 本地单次 attempt 幂等键 |
| `provider_request_id` | text nullable | 供应商请求 ID，用于对账 |
| `principal_id` | text nullable/index | 用户或租户 |
| `request_id` | text/index | 用户操作 |
| `job_id` | FK/index nullable | 正式生成任务 |
| `story_id/story_version_id` | text/index nullable | 作品归属；生成完成后可追加关联事件 |
| `plan_id` | text/index nullable | Plan 及其修订 |
| `episode_id` | text/index nullable | 单集归属 |
| `stage/node` | text/index | planning/generation/performance/validation |
| `attempt_kind` | text | initial_plan、user_plan_revision、first_draft、targeted_repair、json_repair 等 |
| `attempt` | int | Provider 传输尝试序号 |
| `requested_model/actual_model/provider` | text | 路由与实际执行模型 |
| `input_tokens/output_tokens` | bigint nullable | Usage 或估算 |
| `cache_read_tokens/cache_write_tokens` | bigint nullable | 供应商支持时记录 |
| `reasoning_tokens` | bigint nullable | 单独披露；记录是否包含在 output |
| `usage_source` | enum | reported/estimated/unavailable |
| `status/error_code` | text | success/timeout/error/cancelled |
| `latency_ms` | bigint | 本地观测时延 |
| `prompt_version/prompt_hash` | text | 可重现实验和计价归因 |
| `started_at/completed_at` | timestamptz | attempt 时间 |
| `price_revision/currency` | text nullable | 成本计算时采用的价格快照 |
| `estimated_cost` | numeric(24,12) nullable | 本地计算，不冒充已对账 |
| `reconciled_cost` | numeric(24,12) nullable | 供应商账单确认值 |

建议唯一约束：`call_id`；对有稳定供应商 ID 的记录另加 `(provider, provider_request_id)` 条件唯一索引。
失败但没有 provider request ID 时仍保存本地 call ID。

## 3. 价格配置

建议新增 `model_price_versions`：模型 ID、供应商、价格版本、币种、生效时间、失效时间、每百万普通输入、输出、
缓存读、缓存写及可选独立 reasoning 单价。价格记录只追加，不更新旧行。

代码层 `llm/accounting.py` 已提供 `VersionedModelPrice` 和 Decimal 计算。当前不在源码写入具体价格，原因是本项目
使用 OpenAI Compatible 网关，实际合同价可能与模型官方公开价不同；上线前应由采购/财务录入真实合同价格。

## 4. 埋点位置

- Provider 边界：记录每个真实 HTTP attempt、供应商 Usage、错误和 request ID；这是原始事实源。
- `StoryWorkflowService`：注入 principal/request/plan/job 归属，区分首次 Plan 与用户主动 Plan revision。
- `ApprovedPlanLLMExecutor`：区分 first draft 与 targeted duration repair。
- `BaseLLMProvider.complete_structured`：JSON 校验重问必须产生第二个真实调用记录。
- StoryVersion 成功提交后：新增关联事件，把同 job 的账目归属到 story/story_version；不回写或删除原始 attempt。

## 5. 重试和异常计量

| 情况 | 记录方式 | 自动策略 |
|---|---|---|
| HTTP 429/5xx、TLS、timeout | 每次 attempt 一条，Usage unavailable，待供应商账单对账 | 有界退避；不算文学失败 |
| JSON/Schema 非法 | 初次与一次格式重问分别记录 | 最多一次结构修复 |
| 首稿时长越界 | 首稿 + 最多一次单场修复 | 不整集循环重写 |
| 非法角色/重大事实冲突 | 记录失败，Job 失败 | 不自动创意重写 |
| 用户修改 Plan | attempt_kind=user_plan_revision | 用户主动操作，不计为系统返工 |
| 演绎缺行/重音非法 | 只记录待修行调用 | 最多两轮定向修复 |
| 文学审美建议 | 进入离线样本报告 | 线上不自动重写 |

## 6. 汇总接口建议

框架无关服务建议：

```python
get_usage_for_operation(request_id) -> UsageSummary
get_usage_for_job(job_id) -> UsageSummary
get_usage_for_story_version(story_version_id) -> UsageSummary
get_usage_for_principal(principal_id, start_at, end_at) -> UsageSummary
get_usage_by_model(start_at, end_at) -> list[UsageSummary]
reconcile_provider_charge(call_id, actual_amount, invoice_ref) -> ReconciliationRecord
```

`UsageSummary` 同时返回 confirmed cost、estimated cost、unknown call count、各节点 Token、成功/失败调用、重试与修复次数；
绝不只返回一个无法说明可信度的“总费用”。

## 7. 数据库迁移建议

建议在 `004_llm_usage_ledger` 新增三张表：

1. `llm_call_ledger`：不可变调用事实；
2. `model_price_versions`：不可变价格版本；
3. `llm_cost_reconciliations`：供应商账单更正和审核信息。

迁移应在 P0 真实小样本稳定后单独提交。对 PostgreSQL 验证并发幂等、事务失败、进程重启、未知 Usage、对账更正；
SQLite 只验证 repository 行为。不要把账本写入与 StoryVersion 保存放在同一个必须全部成功的事务里，否则主业务回滚会丢失
已发生的供应商成本；推荐独立短事务或可靠 outbox。

## 8. 未来用户计费边界

用户收费可以按故事、集数、目标时长、积分或订阅权益，但不直接等于供应商 Token 成本。建议未来新增独立
`billable_events`，引用 story operation/job 和成本摘要，以产品价格版本计算应收；失败任务是否收费由产品政策决定，原始供应商
成本仍必须保留。支付、余额和退款不属于本轮范围。

## 9. 验收清单

- 成功 reported Usage；缺 Usage 的 estimated；超时 unknown 三类均有测试。
- HTTP 重试和 JSON 重问不会漏记或重复入账。
- reasoning 包含在 output 时不重复计费。
- 模型切换按 actual model 找价格，缺价格进入 unknown/unpriced。
- Plan 用户修订与系统修复可分别汇总。
- 失败 Job 仍能查询已发生调用和未知成本项。
- 跨进程恢复后账目仍存在；重复事件由 call ID 幂等去重。
- 金额 Decimal 精度、币种和价格版本完整保留。
