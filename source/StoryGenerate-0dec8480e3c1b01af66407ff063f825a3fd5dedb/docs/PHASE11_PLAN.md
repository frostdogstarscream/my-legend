# Phase 11 基线审计与实施方案

基线为远端已核对的 `cb93e8be604e7b58f5b02911d11303e6895efb53`，工作分支 `codex/phase11-product-service`。已阅读四操作验收报告、工作流合同和 StoryDelivery v1.0。已有 Python/Pydantic/SQLAlchemy/PostgreSQL；没有 HTTP 框架。Phase7 的 long-form 异步执行器属于另一条流程，本轮不重复复用它来生成四操作正文。

1. 新增 `drama_engine.service`：FastAPI/OpenAPI、认证和统一错误；端点直接调用 StoryWorkflowService。
2. 正文队列继续使用 `story_jobs`，补充租约、心跳与过期令牌恢复。大纲 prepare/revise 增加 `service_plan_tasks`，避免 HTTP 承载模型长调用；Plan 与其任务成功结果原子保存。
3. 角色库复用 `character_templates` 与 `character_profile_revisions`；补充资产引用 JSON，导入版本化、固定事实保护、角色绑定与服务器生成快照。
4. 独立下游适配器生成按句排序的 speech/cue 演绎任务，保留演绎标注，模拟 Consumer 只证明消费合同，不标记真实音频或硬件成功。
5. 新迁移004、部署/请求示例、OpenAPI 快照与联调说明。生产配置必须显式 PostgreSQL 和认证环境变量，无 Mock 回退。
6. 工程测试覆盖跨租户访问、幂等、审批、取消、租约、旧 worker fencing、进程重启、角色版本/绑定与演绎合同；之后通过真实 HTTP 服务与独立 worker 跑真实模型四操作主链，保留逐阶段请求/结果/成本及失败。
7. 不改变已验收创作策略和质量评分，不扩展 Mode/Recipe/模板。最终提交与脱敏证据推送远端，核对SHA并尝试向研发分支建PR，不合并 main。

实现参考：[FastAPI 响应模型与OpenAPI](https://fastapi.tiangolo.com/tutorial/response-model/)，[PostgreSQL16 锁与SKIP LOCKED](https://www.postgresql.org/docs/16/sql-select.html)。
