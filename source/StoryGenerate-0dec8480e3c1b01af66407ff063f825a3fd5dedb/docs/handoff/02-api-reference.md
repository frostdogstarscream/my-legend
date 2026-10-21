# 02 完整 HTTP API 接口手册

服务版本11.0；依据基线 eeb99ae 的实际路由和 OpenAPI。本文由 `tools/handoff/build_reference.py` 生成字段清单，业务解释人工维护。

[机器可读OpenAPI](contracts/openapi.json) · [操作示例](03-manual-demos.md) · [ID、状态与错误处理](07-client-integration.md)

所有 `/v1` 路由使用 `Authorization: Bearer <环境注入密钥>`；仅导入角色要求admin。`Content-Type: application/json` 用于有body的POST/PATCH。不要在body写principal；服务器从凭据映射身份。所有响应有服务器生成的 `X-Request-ID`，不同于业务request_id。没有 `Idempotency-Key` 请求头协议。

[26接口完整成功响应索引](examples/endpoint-success-examples.json)按Method+Path查找，明确区分executed_no_model_http、historical_real_model_http_chain和schema_example_NOT_EXECUTED。后者仅两项（大纲失败重试/正文取消成功态），不能当本轮实测；失败态另有本轮真实HTTP探测。response_reference从仓库根取档案，再读json_pointer。完整字段类型见本文组件表。GET/无body动作的请求JSON为“不适用”；URL即完整请求。

统一异常完整示例：
```json
{"error":{"code":"STALE_PLAN","message":"STALE_PLAN","request_id":"server-correlation-id","details":[]}}
```

通用错误：401 AUTH_REQUIRED；403 ADMIN_REQUIRED（仅导入）；404 NOT_FOUND（含无访问权）；409 幂等/修订/状态冲突；422 SCHEMA_VALIDATION_FAILED 或领域输入码；503 SERVICE_UNAVAILABLE。所有接口均须处理网络超时；超时不代表任务取消。HTTP200的任务FAILED仍是业务失败。

## 完整路由清单

|操作|用途|
|---|---|
|`GET /health`|数据库健康|
|`POST /v1/plans`|准备四操作大纲|
|`GET /v1/tasks/{task_id}`|大纲任务轮询|
|`POST /v1/tasks/{task_id}/cancel`|取消大纲任务|
|`POST /v1/tasks/{task_id}/retry`|显式重试失败大纲|
|`GET /v1/plans/{plan_id}`|获取当前大纲|
|`GET /v1/plans/{plan_id}/revisions`|查询大纲历史|
|`POST /v1/plans/{plan_id}/revisions`|反馈生成新大纲修订|
|`POST /v1/plans/{plan_id}/approve`|审批并原子创建正文任务|
|`POST /v1/plans/{plan_id}/generate`|查询已审批的生成任务|
|`GET /v1/jobs/{job_id}`|正文任务轮询|
|`POST /v1/jobs/{job_id}/cancel`|取消正式生成|
|`POST /v1/jobs/{job_id}/retry`|按原审批快照重试|
|`GET /v1/stories`|列出版本摘要|
|`GET /v1/stories/{story_id}/versions`|同故事历史版本|
|`GET /v1/story-versions/{version_id}`|读取完整故事|
|`GET /v1/story-versions/{version_id}/lineage`|查询来源和衍生分支|
|`GET /v1/story-versions/{version_id}/export`|三种导出|
|`GET /v1/story-versions/{version_id}/render-manifest`|导出演绎队列|
|`POST /v1/story-versions/{version_id}/simulate-tts`|模拟消费验证|
|`POST /v1/characters/import`|角色批量导入与版本更新|
|`GET /v1/characters`|公共角色目录|
|`GET /v1/characters/{character_id}`|角色版本详情|
|`GET /v1/characters/{character_id}/versions`|角色Canon版本历史|
|`GET /v1/characters/{character_id}/runtime`|用户动态参数历史|
|`PATCH /v1/characters/{character_id}/runtime`|追加个性化修订|

合计 **26个 method/path 操作、24个路径**。额外匿名元数据路由：GET `/docs`（Swagger HTML）、`/redoc`（ReDoc HTML）、`/openapi.json`（完整schema）、`/docs/oauth2-redirect`（Swagger OAuth辅助HTML，当前认证仍是Bearer）。不提供登录、播放进度写入、音频合成、Webhook或任务recover HTTP接口。

## GET /health — 数据库健康

**权限**：匿名。

**前置条件**：匿名；只确认数据库可连接，不证明 Worker、模型、TTS 正常。

**参数**：

|位置/名称|类型与限制|必填/默认|业务含义|
|---|---|---|---|

**完整请求**：上述Method和URL替换Path参数，加认证头；没有请求JSON，不发送`{}`。

**成功、异步ID与后续步骤**：200 {"status":"ready","storage":"postgresql"}

**幂等/并发**：只读，无业务幂等键。

**响应合同**：

完整成功JSON：[响应索引](examples/endpoint-success-examples.json)中键 `GET /health`；证据标签说明是否本轮执行/历史真实/仅Schema示例。

- HTTP200：`{"application/json": {"schema": {}}}`

**测试证据/完整示例**：[本轮HTTP探测](contracts/http-probe.json)、[完整探测响应](contracts/http-responses.json)、[Phase11验收](../phase11/ACCEPTANCE.md)。探测逐项标记是否执行；未执行的状态变化只引用既有测试，不冒充真实模型结果。

**失败处理**：适用本文通用错误表；409先重新GET资源判断状态/版本再决定新请求，禁止自动无限retry。422先修正参数；503保留X-Request-ID给运维。

## POST /v1/plans — 准备四操作大纲

**权限**：Bearer，principal隔离的资源需属于调用人。

**前置条件**：来源版本必须属于当前 principal；绑定角色已导入。请求 intent 决定分支；完整四操作真实请求见下方。

**参数**：

|位置/名称|类型与限制|必填/默认|业务含义|
|---|---|---|---|
|body|`{"oneOf": [{"$ref": "#/components/schemas/CreateStoryRequest"}, {"$ref": "#/components/schemas/ContinueStoryRequest"}, {"$ref": "#/components/schemas/ReviseStoryRequest"}, {"$ref": "#/components/schemas/RemixStoryRequest"}], "title": "Value", "discriminator": {"propertyName": "intent", "mapping": {"create": "#/components/schemas/CreateStoryRequest", "continue": "#/components/schemas/ContinueStoryRequest", "revise": "#/components/schemas/ReviseStoryRequest", "remix": "#/components/schemas/RemixStoryRequest"}}}`|必填|下方完整示例；所有字段见组件表|

**完整请求JSON**：
[四操作真实请求](examples/create.real.json)、[Revise](examples/revise.real.json)、[Continue](examples/continue.real.json)、[Remix](examples/remix.real.json)；异地重放须替换来源/角色及全新ID。

**成功、异步ID与后续步骤**：202 TaskView；保存 task_id，轮询到 SUCCEEDED 后从 result 获取 plan_id。

**幂等/并发**：principal+body.idempotency_key；相同键相同内容重用任务，变更内容409 IDEMPOTENCY_KEY_CONFLICT；request_id 重用但新键可触发 REQUEST_ID_CONFLICT。

**响应合同**：

完整成功JSON：[响应索引](examples/endpoint-success-examples.json)中键 `POST /v1/plans`；证据标签说明是否本轮执行/历史真实/仅Schema示例。

- HTTP202：`{"application/json": {"schema": {"$ref": "#/components/schemas/TaskView"}}}`

**完整真实响应及历史测试证据**：[原始HTTP记录](../../6-实测产物/phase11_live_20261015_v3/create/submit_before_restart.json)（response字段；请求字段可一起核对）。

**失败处理**：适用本文通用错误表；409先重新GET资源判断状态/版本再决定新请求，禁止自动无限retry。422先修正参数；503保留X-Request-ID给运维。

## GET /v1/tasks/{task_id} — 大纲任务轮询

**权限**：Bearer，principal隔离的资源需属于调用人。

**前置条件**：使用 prepare/revisions 返回的 task_id。

**参数**：

|位置/名称|类型与限制|必填/默认|业务含义|
|---|---|---|---|
|path/task_id|`{"title": "Task Id"}`|必填|大纲队列ID|

**完整请求**：上述Method和URL替换Path参数，加认证头；没有请求JSON，不发送`{}`。

**成功、异步ID与后续步骤**：200 TaskView；FAILED 仍是HTTP200，查看 error_code/error_message。

**幂等/并发**：只读；跨 principal 返回404。

**响应合同**：

完整成功JSON：[响应索引](examples/endpoint-success-examples.json)中键 `GET /v1/tasks/{task_id}`；证据标签说明是否本轮执行/历史真实/仅Schema示例。

- HTTP200：`{"application/json": {"schema": {"$ref": "#/components/schemas/TaskView"}}}`

**完整真实响应及历史测试证据**：[原始HTTP记录](../../6-实测产物/phase11_live_20261015_v3/create/planning_task.json)（response字段；请求字段可一起核对）。

**失败处理**：适用本文通用错误表；409先重新GET资源判断状态/版本再决定新请求，禁止自动无限retry。422先修正参数；503保留X-Request-ID给运维。

## POST /v1/tasks/{task_id}/cancel — 取消大纲任务

**权限**：Bearer，principal隔离的资源需属于调用人。

**前置条件**：QUEUED/RUNNING 可取消。

**参数**：

|位置/名称|类型与限制|必填/默认|业务含义|
|---|---|---|---|
|path/task_id|`{"title": "Task Id"}`|必填|大纲队列ID|

**完整请求**：上述Method和URL替换Path参数，加认证头；没有请求JSON，不发送`{}`。

**成功、异步ID与后续步骤**：200 TaskView；排队直接 CANCELLED，运行中 CANCEL_REQUESTED 后等待终态。

**幂等/并发**：CANCELLED 可重复取消；CANCEL_REQUESTED 重复调用可能409 TASK_NOT_CANCELLABLE，请轮询。

**响应合同**：

完整成功JSON：[响应索引](examples/endpoint-success-examples.json)中键 `POST /v1/tasks/{task_id}/cancel`；证据标签说明是否本轮执行/历史真实/仅Schema示例。

- HTTP200：`{"application/json": {"schema": {"$ref": "#/components/schemas/TaskView"}}}`

**测试证据/完整示例**：[本轮HTTP探测](contracts/http-probe.json)、[完整探测响应](contracts/http-responses.json)、[Phase11验收](../phase11/ACCEPTANCE.md)。探测逐项标记是否执行；未执行的状态变化只引用既有测试，不冒充真实模型结果。

**失败处理**：适用本文通用错误表；409先重新GET资源判断状态/版本再决定新请求，禁止自动无限retry。422先修正参数；503保留X-Request-ID给运维。

## POST /v1/tasks/{task_id}/retry — 显式重试失败大纲

**权限**：Bearer，principal隔离的资源需属于调用人。

**前置条件**：只允许 FAILED；CANCELLED 不可重试。

**参数**：

|位置/名称|类型与限制|必填/默认|业务含义|
|---|---|---|---|
|path/task_id|`{"title": "Task Id"}`|必填|大纲队列ID|

**完整请求**：上述Method和URL替换Path参数，加认证头；没有请求JSON，不发送`{}`。

**成功、异步ID与后续步骤**：202 同一 task_id 回到 QUEUED；再次轮询。

**幂等/并发**：无请求体；重复调用若已QUEUED返回409，不自动重试计费调用。

**响应合同**：

完整成功JSON：[响应索引](examples/endpoint-success-examples.json)中键 `POST /v1/tasks/{task_id}/retry`；证据标签说明是否本轮执行/历史真实/仅Schema示例。

- HTTP202：`{"application/json": {"schema": {"$ref": "#/components/schemas/TaskView"}}}`

**测试证据/完整示例**：[本轮HTTP探测](contracts/http-probe.json)、[完整探测响应](contracts/http-responses.json)、[Phase11验收](../phase11/ACCEPTANCE.md)。探测逐项标记是否执行；未执行的状态变化只引用既有测试，不冒充真实模型结果。

**失败处理**：适用本文通用错误表；409先重新GET资源判断状态/版本再决定新请求，禁止自动无限retry。422先修正参数；503保留X-Request-ID给运维。

## GET /v1/plans/{plan_id} — 获取当前大纲

**权限**：Bearer，principal隔离的资源需属于调用人。

**前置条件**：大纲任务 SUCCEEDED 后获取 plan_id。

**参数**：

|位置/名称|类型与限制|必填/默认|业务含义|
|---|---|---|---|
|path/plan_id|`{"title": "Plan Id"}`|必填|大纲ID|

**完整请求**：上述Method和URL替换Path参数，加认证头；没有请求JSON，不发送`{}`。

**成功、异步ID与后续步骤**：200 PlanPreview；展示 outline、角色快照、revision、fingerprint 和影响范围。

**幂等/并发**：只读；审批前必须重新获取最新内容。

**响应合同**：

完整成功JSON：[响应索引](examples/endpoint-success-examples.json)中键 `GET /v1/plans/{plan_id}`；证据标签说明是否本轮执行/历史真实/仅Schema示例。

- HTTP200：`{"application/json": {"schema": {"$ref": "#/components/schemas/PlanPreview"}}}`

**完整真实响应及历史测试证据**：[原始HTTP记录](../../6-实测产物/phase11_live_20261015_v3/create/revised_plan.json)（response字段；请求字段可一起核对）。

**失败处理**：适用本文通用错误表；409先重新GET资源判断状态/版本再决定新请求，禁止自动无限retry。422先修正参数；503保留X-Request-ID给运维。

## GET /v1/plans/{plan_id}/revisions — 查询大纲历史

**权限**：Bearer，principal隔离的资源需属于调用人。

**前置条件**：当前 principal 可访问该 Plan。

**参数**：

|位置/名称|类型与限制|必填/默认|业务含义|
|---|---|---|---|
|path/plan_id|`{"title": "Plan Id"}`|必填|大纲ID|

**完整请求**：上述Method和URL替换Path参数，加认证头；没有请求JSON，不发送`{}`。

**成功、异步ID与后续步骤**：200 PlanPreview[]，按 revision 升序。

**幂等/并发**：只读，历史快照不可变。

**响应合同**：

完整成功JSON：[响应索引](examples/endpoint-success-examples.json)中键 `GET /v1/plans/{plan_id}/revisions`；证据标签说明是否本轮执行/历史真实/仅Schema示例。

- HTTP200：`{"application/json": {"schema": {"type": "array", "items": {"$ref": "#/components/schemas/PlanPreview"}, "title": "Response Revisions V1 Plans  Plan Id  Revisions Get"}}}`

**测试证据/完整示例**：[本轮HTTP探测](contracts/http-probe.json)、[完整探测响应](contracts/http-responses.json)、[Phase11验收](../phase11/ACCEPTANCE.md)。探测逐项标记是否执行；未执行的状态变化只引用既有测试，不冒充真实模型结果。

**失败处理**：适用本文通用错误表；409先重新GET资源判断状态/版本再决定新请求，禁止自动无限retry。422先修正参数；503保留X-Request-ID给运维。

## POST /v1/plans/{plan_id}/revisions — 反馈生成新大纲修订

**权限**：Bearer，principal隔离的资源需属于调用人。

**前置条件**：Plan 必须 AWAITING_APPROVAL；expected_revision 为最新值。APPROVED 不支持原地修改。

**参数**：

|位置/名称|类型与限制|必填/默认|业务含义|
|---|---|---|---|
|path/plan_id|`{"title": "Plan Id"}`|必填|大纲ID|
|body|`{"$ref": "#/components/schemas/PlanRevisionRequest"}`|必填|下方完整示例；所有字段见组件表|

**完整请求JSON**：
[Plan反馈](examples/plan.revision.json)

**成功、异步ID与后续步骤**：202 TaskView；完成后重新GET Plan，核对revision增加，再审批。

**幂等/并发**：大纲队列幂等键与prepare共享命名空间；过期修订409 STALE_PLAN。

**响应合同**：

完整成功JSON：[响应索引](examples/endpoint-success-examples.json)中键 `POST /v1/plans/{plan_id}/revisions`；证据标签说明是否本轮执行/历史真实/仅Schema示例。

- HTTP202：`{"application/json": {"schema": {"$ref": "#/components/schemas/TaskView"}}}`

**完整真实响应及历史测试证据**：[原始HTTP记录](../../6-实测产物/phase11_live_20261015_v3/create/revision_submit.json)（response字段；请求字段可一起核对）。

**失败处理**：适用本文通用错误表；409先重新GET资源判断状态/版本再决定新请求，禁止自动无限retry。422先修正参数；503保留X-Request-ID给运维。

## POST /v1/plans/{plan_id}/approve — 审批并原子创建正文任务

**权限**：Bearer，principal隔离的资源需属于调用人。

**前置条件**：人工确认最新 Plan；提交 expected_revision 和 fingerprint（取自 plan_fingerprint）。角色快照不能过期。

**参数**：

|位置/名称|类型与限制|必填/默认|业务含义|
|---|---|---|---|
|path/plan_id|`{"title": "Plan Id"}`|必填|大纲ID|
|body|`{"$ref": "#/components/schemas/PlanApprovalRequest"}`|必填|下方完整示例；所有字段见组件表|

**完整请求JSON**：
[审批](examples/plan.approval.json)，fingerprint必须用刚读取的大纲值替换64个0。

**成功、异步ID与后续步骤**：202 JobView；审批与 QUEUED Job 在同一事务提交，保存job_id。

**幂等/并发**：审批幂等键独立于大纲队列；过期 revision/fingerprint409，角色版本变化409。

**响应合同**：

完整成功JSON：[响应索引](examples/endpoint-success-examples.json)中键 `POST /v1/plans/{plan_id}/approve`；证据标签说明是否本轮执行/历史真实/仅Schema示例。

- HTTP202：`{"application/json": {"schema": {"$ref": "#/components/schemas/JobView"}}}`

**完整真实响应及历史测试证据**：[原始HTTP记录](../../6-实测产物/phase11_live_20261015_v3/create/approval.json)（response字段；请求字段可一起核对）。

**失败处理**：适用本文通用错误表；409先重新GET资源判断状态/版本再决定新请求，禁止自动无限retry。422先修正参数；503保留X-Request-ID给运维。

## POST /v1/plans/{plan_id}/generate — 查询已审批的生成任务

**权限**：Bearer，principal隔离的资源需属于调用人。

**前置条件**：APPROVED 且已有关联 Job。没有请求体。

**参数**：

|位置/名称|类型与限制|必填/默认|业务含义|
|---|---|---|---|
|path/plan_id|`{"title": "Plan Id"}`|必填|大纲ID|

**完整请求**：上述Method和URL替换Path参数，加认证头；没有请求JSON，不发送`{}`。

**成功、异步ID与后续步骤**：202 JobView；只取审批时的Job，不另建生成任务。

**幂等/并发**：未审批409 PLAN_APPROVAL_REQUIRED；重复调用不多次发布。

**响应合同**：

完整成功JSON：[响应索引](examples/endpoint-success-examples.json)中键 `POST /v1/plans/{plan_id}/generate`；证据标签说明是否本轮执行/历史真实/仅Schema示例。

- HTTP202：`{"application/json": {"schema": {"$ref": "#/components/schemas/JobView"}}}`

**完整真实响应及历史测试证据**：[原始HTTP记录](../../6-实测产物/phase11_live_20261015_v3/create/approval_idempotent.json)（response字段；请求字段可一起核对）。

**失败处理**：适用本文通用错误表；409先重新GET资源判断状态/版本再决定新请求，禁止自动无限retry。422先修正参数；503保留X-Request-ID给运维。

## GET /v1/jobs/{job_id} — 正文任务轮询

**权限**：Bearer，principal隔离的资源需属于调用人。

**前置条件**：审批返回的 job_id。

**参数**：

|位置/名称|类型与限制|必填/默认|业务含义|
|---|---|---|---|
|path/job_id|`{"title": "Job Id"}`|必填|正文队列ID|

**完整请求**：上述Method和URL替换Path参数，加认证头；没有请求JSON，不发送`{}`。

**成功、异步ID与后续步骤**：200 JobView；SUCCEEDED后用result_story_version_id取正文。

**幂等/并发**：只读；进度是执行阶段提示，不是剩余时间保证。

**响应合同**：

完整成功JSON：[响应索引](examples/endpoint-success-examples.json)中键 `GET /v1/jobs/{job_id}`；证据标签说明是否本轮执行/历史真实/仅Schema示例。

- HTTP200：`{"application/json": {"schema": {"$ref": "#/components/schemas/JobView"}}}`

**完整真实响应及历史测试证据**：[原始HTTP记录](../../6-实测产物/phase11_live_20261015_v3/create/generation_attempt2.json)（response字段；请求字段可一起核对）。

**失败处理**：适用本文通用错误表；409先重新GET资源判断状态/版本再决定新请求，禁止自动无限retry。422先修正参数；503保留X-Request-ID给运维。

## POST /v1/jobs/{job_id}/cancel — 取消正式生成

**权限**：Bearer，principal隔离的资源需属于调用人。

**前置条件**：排队/运行/校验/取消中可调用。

**参数**：

|位置/名称|类型与限制|必填/默认|业务含义|
|---|---|---|---|
|path/job_id|`{"title": "Job Id"}`|必填|正文队列ID|

**完整请求**：上述Method和URL替换Path参数，加认证头；没有请求JSON，不发送`{}`。

**成功、异步ID与后续步骤**：200 JobView；等待 CANCELLED，不能把202/200理解为模型已停止。

**幂等/并发**：CANCELLED重复调用可返回同状态；成功/失败任务409 JOB_NOT_CANCELLABLE。

**响应合同**：

完整成功JSON：[响应索引](examples/endpoint-success-examples.json)中键 `POST /v1/jobs/{job_id}/cancel`；证据标签说明是否本轮执行/历史真实/仅Schema示例。

- HTTP200：`{"application/json": {"schema": {"$ref": "#/components/schemas/JobView"}}}`

**测试证据/完整示例**：[本轮HTTP探测](contracts/http-probe.json)、[完整探测响应](contracts/http-responses.json)、[Phase11验收](../phase11/ACCEPTANCE.md)。探测逐项标记是否执行；未执行的状态变化只引用既有测试，不冒充真实模型结果。

**失败处理**：适用本文通用错误表；409先重新GET资源判断状态/版本再决定新请求，禁止自动无限retry。422先修正参数；503保留X-Request-ID给运维。

## POST /v1/jobs/{job_id}/retry — 按原审批快照重试

**权限**：Bearer，principal隔离的资源需属于调用人。

**前置条件**：只允许 FAILED；先阅读错误和成本记录。

**参数**：

|位置/名称|类型与限制|必填/默认|业务含义|
|---|---|---|---|
|path/job_id|`{"title": "Job Id"}`|必填|正文队列ID|

**完整请求**：上述Method和URL替换Path参数，加认证头；没有请求JSON，不发送`{}`。

**成功、异步ID与后续步骤**：202 JobView，同job_id再次排队；attempt_history保留失败记录。

**幂等/并发**：无请求体；CANCELLED不可重试。若变更要求，必须新Plan而非retry。

**响应合同**：

完整成功JSON：[响应索引](examples/endpoint-success-examples.json)中键 `POST /v1/jobs/{job_id}/retry`；证据标签说明是否本轮执行/历史真实/仅Schema示例。

- HTTP202：`{"application/json": {"schema": {"$ref": "#/components/schemas/JobView"}}}`

**完整真实响应及历史测试证据**：[原始HTTP记录](../../6-实测产物/phase11_live_20261015_v3/create/retry.json)（response字段；请求字段可一起核对）。

**失败处理**：适用本文通用错误表；409先重新GET资源判断状态/版本再决定新请求，禁止自动无限retry。422先修正参数；503保留X-Request-ID给运维。

## GET /v1/stories — 列出版本摘要

**权限**：Bearer，principal隔离的资源需属于调用人。

**前置条件**：当前 principal。

**参数**：

|位置/名称|类型与限制|必填/默认|业务含义|
|---|---|---|---|
|query/limit|`{"type": "integer", "maximum": 200, "minimum": 1, "default": 50, "title": "Limit"}`|可选|每页条数|
|query/offset|`{"type": "integer", "minimum": 0, "default": 0, "title": "Offset"}`|可选|跳过条数|

**完整请求**：上述Method和URL替换Path参数，加认证头；没有请求JSON，不发送`{}`。

**成功、异步ID与后续步骤**：200 数组；每行是版本，多行可共享story_id。

**幂等/并发**：只读；limit/offset分页，不保证并发写入期间稳定游标。

**响应合同**：

完整成功JSON：[响应索引](examples/endpoint-success-examples.json)中键 `GET /v1/stories`；证据标签说明是否本轮执行/历史真实/仅Schema示例。

- HTTP200：`{"application/json": {"schema": {}}}`

**测试证据/完整示例**：[本轮HTTP探测](contracts/http-probe.json)、[完整探测响应](contracts/http-responses.json)、[Phase11验收](../phase11/ACCEPTANCE.md)。探测逐项标记是否执行；未执行的状态变化只引用既有测试，不冒充真实模型结果。

**失败处理**：适用本文通用错误表；409先重新GET资源判断状态/版本再决定新请求，禁止自动无限retry。422先修正参数；503保留X-Request-ID给运维。

## GET /v1/stories/{story_id}/versions — 同故事历史版本

**权限**：Bearer，principal隔离的资源需属于调用人。

**前置条件**：已发布story_id。

**参数**：

|位置/名称|类型与限制|必填/默认|业务含义|
|---|---|---|---|
|path/story_id|`{"title": "Story Id"}`|必填|同故事分组|

**完整请求**：上述Method和URL替换Path参数，加认证头；没有请求JSON，不发送`{}`。

**成功、异步ID与后续步骤**：200 StoryDelivery[]，version_no升序；不存在404。

**幂等/并发**：历史派生版本号按数据库串行分配，不能推定parent.version_no+1。

**响应合同**：

完整成功JSON：[响应索引](examples/endpoint-success-examples.json)中键 `GET /v1/stories/{story_id}/versions`；证据标签说明是否本轮执行/历史真实/仅Schema示例。

- HTTP200：`{"application/json": {"schema": {"type": "array", "items": {"$ref": "#/components/schemas/StoryDelivery"}, "title": "Response Versions V1 Stories  Story Id  Versions Get"}}}`

**测试证据/完整示例**：[本轮HTTP探测](contracts/http-probe.json)、[完整探测响应](contracts/http-responses.json)、[Phase11验收](../phase11/ACCEPTANCE.md)。探测逐项标记是否执行；未执行的状态变化只引用既有测试，不冒充真实模型结果。

**失败处理**：适用本文通用错误表；409先重新GET资源判断状态/版本再决定新请求，禁止自动无限retry。422先修正参数；503保留X-Request-ID给运维。

## GET /v1/story-versions/{version_id} — 读取完整故事

**权限**：Bearer，principal隔离的资源需属于调用人。

**前置条件**：Job已SUCCEEDED，保存result_story_version_id。

**参数**：

|位置/名称|类型与限制|必填/默认|业务含义|
|---|---|---|---|
|path/version_id|`{"title": "Version Id"}`|必填|不可变故事版本|

**完整请求**：上述Method和URL替换Path参数，加认证头；没有请求JSON，不发送`{}`。

**成功、异步ID与后续步骤**：200 StoryDelivery 1.0；完整旧集与新集，不是增量事件流。

**幂等/并发**：不可变版本；无ETag写入协议。

**响应合同**：

完整成功JSON：[响应索引](examples/endpoint-success-examples.json)中键 `GET /v1/story-versions/{version_id}`；证据标签说明是否本轮执行/历史真实/仅Schema示例。

- HTTP200：`{"application/json": {"schema": {"$ref": "#/components/schemas/StoryDelivery"}}}`

**完整真实响应及历史测试证据**：[原始HTTP记录](../../6-实测产物/phase11_live_20261015_v3/continue/delivery.json)（response字段；请求字段可一起核对）。

**失败处理**：适用本文通用错误表；409先重新GET资源判断状态/版本再决定新请求，禁止自动无限retry。422先修正参数；503保留X-Request-ID给运维。

## GET /v1/story-versions/{version_id}/lineage — 查询来源和衍生分支

**权限**：Bearer，principal隔离的资源需属于调用人。

**前置条件**：可访问version_id。

**参数**：

|位置/名称|类型与限制|必填/默认|业务含义|
|---|---|---|---|
|path/version_id|`{"title": "Version Id"}`|必填|不可变故事版本|

**完整请求**：上述Method和URL替换Path参数，加认证头；没有请求JSON，不发送`{}`。

**成功、异步ID与后续步骤**：200 {root_requested_version_id, versions:[{story_id,story_version_id,version_no,parent_story_version_id,lineage_type}]}。

**幂等/并发**：只读；遍历连通祖先、后代和同源分支，包含不同story_id的Remix。

**响应合同**：

完整成功JSON：[响应索引](examples/endpoint-success-examples.json)中键 `GET /v1/story-versions/{version_id}/lineage`；证据标签说明是否本轮执行/历史真实/仅Schema示例。

- HTTP200：`{"application/json": {"schema": {}}}`

**测试证据/完整示例**：[本轮HTTP探测](contracts/http-probe.json)、[完整探测响应](contracts/http-responses.json)、[Phase11验收](../phase11/ACCEPTANCE.md)。探测逐项标记是否执行；未执行的状态变化只引用既有测试，不冒充真实模型结果。

**失败处理**：适用本文通用错误表；409先重新GET资源判断状态/版本再决定新请求，禁止自动无限retry。422先修正参数；503保留X-Request-ID给运维。

## GET /v1/story-versions/{version_id}/export — 三种导出

**权限**：Bearer，principal隔离的资源需属于调用人。

**前置条件**：READY版本；format=json/script/characters，默认json。

**参数**：

|位置/名称|类型与限制|必填/默认|业务含义|
|---|---|---|---|
|path/version_id|`{"title": "Version Id"}`|必填|不可变故事版本|
|query/format|`{"type": "string", "pattern": "^(json|script|characters)$", "default": "json", "title": "Format"}`|可选|json全文/script台本/characters映射|

**完整请求**：上述Method和URL替换Path参数，加认证头；没有请求JSON，不发送`{}`。

**成功、异步ID与后续步骤**：json为完整StoryDelivery；script为text/plain完整台本；characters为角色映射JSON。

**幂等/并发**：只读；OpenAPI此动态响应不完全描述各媒体类型，按format解析。

**响应合同**：

完整成功JSON：[响应索引](examples/endpoint-success-examples.json)中键 `GET /v1/story-versions/{version_id}/export`；证据标签说明是否本轮执行/历史真实/仅Schema示例。

- HTTP200：`{"application/json": {"schema": {}}}`

**完整真实响应及历史测试证据**：[原始HTTP记录](../../6-实测产物/phase11_live_20261015_v3/create/export.json)（response字段；请求字段可一起核对）。

**失败处理**：适用本文通用错误表；409先重新GET资源判断状态/版本再决定新请求，禁止自动无限retry。422先修正参数；503保留X-Request-ID给运维。

## GET /v1/story-versions/{version_id}/render-manifest — 导出演绎队列

**权限**：Bearer，principal隔离的资源需属于调用人。

**前置条件**：READY且通过完整性校验。

**参数**：

|位置/名称|类型与限制|必填/默认|业务含义|
|---|---|---|---|
|path/version_id|`{"title": "Version Id"}`|必填|不可变故事版本|

**完整请求**：上述Method和URL替换Path参数，加认证头；没有请求JSON，不发送`{}`。

**成功、异步ID与后续步骤**：200 RenderManifest；有序speech/cue任务，无真实音频。

**幂等/并发**：同版本同位置task_id稳定，新版本会改变。

**响应合同**：

完整成功JSON：[响应索引](examples/endpoint-success-examples.json)中键 `GET /v1/story-versions/{version_id}/render-manifest`；证据标签说明是否本轮执行/历史真实/仅Schema示例。

- HTTP200：`{"application/json": {"schema": {"$ref": "#/components/schemas/RenderManifest"}}}`

**完整真实响应及历史测试证据**：[原始HTTP记录](../../6-实测产物/phase11_live_20261015_v3/create/render.json)（response字段；请求字段可一起核对）。

**失败处理**：适用本文通用错误表；409先重新GET资源判断状态/版本再决定新请求，禁止自动无限retry。422先修正参数；503保留X-Request-ID给运维。

## POST /v1/story-versions/{version_id}/simulate-tts — 模拟消费验证

**权限**：Bearer，principal隔离的资源需属于调用人。

**前置条件**：READY；不调用真实TTS。无请求体。

**参数**：

|位置/名称|类型与限制|必填/默认|业务含义|
|---|---|---|---|
|path/version_id|`{"title": "Version Id"}`|必填|不可变故事版本|

**完整请求**：上述Method和URL替换Path参数，加认证头；没有请求JSON，不发送`{}`。

**成功、异步ID与后续步骤**：200 simulation_only=true；audio_state仍NOT_SYNTHESIZED。

**幂等/并发**：确定性契约验证，可重复；不写音频状态。

**响应合同**：

完整成功JSON：[响应索引](examples/endpoint-success-examples.json)中键 `POST /v1/story-versions/{version_id}/simulate-tts`；证据标签说明是否本轮执行/历史真实/仅Schema示例。

- HTTP200：`{"application/json": {"schema": {}}}`

**完整真实响应及历史测试证据**：[原始HTTP记录](../../6-实测产物/phase11_live_20261015_v3/create/simulation.json)（response字段；请求字段可一起核对）。

**失败处理**：适用本文通用错误表；409先重新GET资源判断状态/版本再决定新请求，禁止自动无限retry。422先修正参数；503保留X-Request-ID给运维。

## POST /v1/characters/import — 角色批量导入与版本更新

**权限**：admin Bearer。

**前置条件**：admin；批内character_id唯一；首次expected_version=0。

**参数**：

|位置/名称|类型与限制|必填/默认|业务含义|
|---|---|---|---|
|body|`{"$ref": "#/components/schemas/CharacterBatch"}`|必填|下方完整示例；所有字段见组件表|

**完整请求JSON**：
[角色导入](examples/forest.characters.json)

**成功、异步ID与后续步骤**：200 CharacterView[]；整批事务，错误不部分提交。

**幂等/并发**：expected_version乐观并发；固定Canon不可覆写；无业务幂等键，重放已成功导入会版本冲突。

**响应合同**：

完整成功JSON：[响应索引](examples/endpoint-success-examples.json)中键 `POST /v1/characters/import`；证据标签说明是否本轮执行/历史真实/仅Schema示例。

- HTTP200：`{"application/json": {"schema": {"items": {"$ref": "#/components/schemas/CharacterView"}, "type": "array", "title": "Response Import Characters V1 Characters Import Post"}}}`

**测试证据/完整示例**：[本轮HTTP探测](contracts/http-probe.json)、[完整探测响应](contracts/http-responses.json)、[Phase11验收](../phase11/ACCEPTANCE.md)。探测逐项标记是否执行；未执行的状态变化只引用既有测试，不冒充真实模型结果。

**失败处理**：适用本文通用错误表；409先重新GET资源判断状态/版本再决定新请求，禁止自动无限retry。422先修正参数；503保留X-Request-ID给运维。

## GET /v1/characters — 公共角色目录

**权限**：Bearer，principal隔离的资源需属于调用人。

**前置条件**：已认证；Canon目录跨principal共享，Runtime不共享。

**参数**：

|位置/名称|类型与限制|必填/默认|业务含义|
|---|---|---|---|
|query/limit|`{"type": "integer", "maximum": 200, "minimum": 1, "default": 50, "title": "Limit"}`|可选|每页条数|
|query/offset|`{"type": "integer", "minimum": 0, "default": 0, "title": "Offset"}`|可选|跳过条数|

**完整请求**：上述Method和URL替换Path参数，加认证头；没有请求JSON，不发送`{}`。

**成功、异步ID与后续步骤**：200 CharacterView[]；active角色最新版本，limit/offset分页。

**幂等/并发**：只读。

**响应合同**：

完整成功JSON：[响应索引](examples/endpoint-success-examples.json)中键 `GET /v1/characters`；证据标签说明是否本轮执行/历史真实/仅Schema示例。

- HTTP200：`{"application/json": {"schema": {"type": "array", "items": {"$ref": "#/components/schemas/CharacterView"}, "title": "Response Characters V1 Characters Get"}}}`

**测试证据/完整示例**：[本轮HTTP探测](contracts/http-probe.json)、[完整探测响应](contracts/http-responses.json)、[Phase11验收](../phase11/ACCEPTANCE.md)。探测逐项标记是否执行；未执行的状态变化只引用既有测试，不冒充真实模型结果。

**失败处理**：适用本文通用错误表；409先重新GET资源判断状态/版本再决定新请求，禁止自动无限retry。422先修正参数；503保留X-Request-ID给运维。

## GET /v1/characters/{character_id} — 角色版本详情

**权限**：Bearer，principal隔离的资源需属于调用人。

**前置条件**：已认证；version_number省略取最新，提供>=1取历史。

**参数**：

|位置/名称|类型与限制|必填/默认|业务含义|
|---|---|---|---|
|path/character_id|`{"title": "Character Id"}`|必填|角色库ID|
|query/version_number|`{"anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}], "title": "Version Number"}`|可选|指定Canon历史版本；省略取最新|

**完整请求**：上述Method和URL替换Path参数，加认证头；没有请求JSON，不发送`{}`。

**成功、异步ID与后续步骤**：200 CharacterView；未知角色/版本404。

**幂等/并发**：只读；故事已保存快照不受后续库变更影响。

**响应合同**：

完整成功JSON：[响应索引](examples/endpoint-success-examples.json)中键 `GET /v1/characters/{character_id}`；证据标签说明是否本轮执行/历史真实/仅Schema示例。

- HTTP200：`{"application/json": {"schema": {"$ref": "#/components/schemas/CharacterView"}}}`

**测试证据/完整示例**：[本轮HTTP探测](contracts/http-probe.json)、[完整探测响应](contracts/http-responses.json)、[Phase11验收](../phase11/ACCEPTANCE.md)。探测逐项标记是否执行；未执行的状态变化只引用既有测试，不冒充真实模型结果。

**失败处理**：适用本文通用错误表；409先重新GET资源判断状态/版本再决定新请求，禁止自动无限retry。422先修正参数；503保留X-Request-ID给运维。

## GET /v1/characters/{character_id}/versions — 角色Canon版本历史

**权限**：Bearer，principal隔离的资源需属于调用人。

**前置条件**：已认证；角色存在。

**参数**：

|位置/名称|类型与限制|必填/默认|业务含义|
|---|---|---|---|
|path/character_id|`{"title": "Character Id"}`|必填|角色库ID|

**完整请求**：上述Method和URL替换Path参数，加认证头；没有请求JSON，不发送`{}`。

**成功、异步ID与后续步骤**：200 CharacterView[]，按version升序。

**幂等/并发**：只读；不是用户Runtime版本。

**响应合同**：

完整成功JSON：[响应索引](examples/endpoint-success-examples.json)中键 `GET /v1/characters/{character_id}/versions`；证据标签说明是否本轮执行/历史真实/仅Schema示例。

- HTTP200：`{"application/json": {"schema": {"type": "array", "items": {"$ref": "#/components/schemas/CharacterView"}, "title": "Response Character Versions V1 Characters  Character Id  Versions Get"}}}`

**测试证据/完整示例**：[本轮HTTP探测](contracts/http-probe.json)、[完整探测响应](contracts/http-responses.json)、[Phase11验收](../phase11/ACCEPTANCE.md)。探测逐项标记是否执行；未执行的状态变化只引用既有测试，不冒充真实模型结果。

**失败处理**：适用本文通用错误表；409先重新GET资源判断状态/版本再决定新请求，禁止自动无限retry。422先修正参数；503保留X-Request-ID给运维。

## GET /v1/characters/{character_id}/runtime — 用户动态参数历史

**权限**：Bearer，principal隔离的资源需属于调用人。

**前置条件**：当前principal；角色已导入。

**参数**：

|位置/名称|类型与限制|必填/默认|业务含义|
|---|---|---|---|
|path/character_id|`{"title": "Character Id"}`|必填|角色库ID|

**完整请求**：上述Method和URL替换Path参数，加认证头；没有请求JSON，不发送`{}`。

**成功、异步ID与后续步骤**：200 RuntimeView[]，revision升序；未个性化时可为空。

**幂等/并发**：principal隔离，空历史下首次PATCH expected_revision=0。

**响应合同**：

完整成功JSON：[响应索引](examples/endpoint-success-examples.json)中键 `GET /v1/characters/{character_id}/runtime`；证据标签说明是否本轮执行/历史真实/仅Schema示例。

- HTTP200：`{"application/json": {"schema": {"type": "array", "items": {"$ref": "#/components/schemas/RuntimeView"}, "title": "Response Runtime Versions V1 Characters  Character Id  Runtime Get"}}}`

**测试证据/完整示例**：[本轮HTTP探测](contracts/http-probe.json)、[完整探测响应](contracts/http-responses.json)、[Phase11验收](../phase11/ACCEPTANCE.md)。探测逐项标记是否执行；未执行的状态变化只引用既有测试，不冒充真实模型结果。

**失败处理**：适用本文通用错误表；409先重新GET资源判断状态/版本再决定新请求，禁止自动无限retry。422先修正参数；503保留X-Request-ID给运维。

## PATCH /v1/characters/{character_id}/runtime — 追加个性化修订

**权限**：Bearer，principal隔离的资源需属于调用人。

**前置条件**：expected_revision为当前最大值或首次0；只允许Runtime字段。

**参数**：

|位置/名称|类型与限制|必填/默认|业务含义|
|---|---|---|---|
|path/character_id|`{"title": "Character Id"}`|必填|角色库ID|
|body|`{"$ref": "#/components/schemas/RuntimeUpdate"}`|必填|下方完整示例；所有字段见组件表|

**完整请求JSON**：
[Runtime更新](examples/runtime.patch.json)

**成功、异步ID与后续步骤**：200 RuntimeView；保存新revision。

**幂等/并发**：并发冲突409；Canonical身份字段不得放进patch，额外字段422。

**响应合同**：

完整成功JSON：[响应索引](examples/endpoint-success-examples.json)中键 `PATCH /v1/characters/{character_id}/runtime`；证据标签说明是否本轮执行/历史真实/仅Schema示例。

- HTTP200：`{"application/json": {"schema": {"$ref": "#/components/schemas/RuntimeView"}}}`

**测试证据/完整示例**：[本轮HTTP探测](contracts/http-probe.json)、[完整探测响应](contracts/http-responses.json)、[Phase11验收](../phase11/ACCEPTANCE.md)。探测逐项标记是否执行；未执行的状态变化只引用既有测试，不冒充真实模型结果。

**失败处理**：适用本文通用错误表；409先重新GET资源判断状态/版本再决定新请求，禁止自动无限retry。422先修正参数；503保留X-Request-ID给运维。

## 全部输入/输出 Schema 字段

以下表直接展开OpenAPI components：类型、必填、默认、enum/const、上下限、引用与嵌套结构均保留在约束列。`$ref` 指向本节同名模型；数组items、anyOf、oneOf按原Schema解释。未列default的可选字段可能由服务生成或default_factory构造，不代表默认null。输出字段的业务含义依名称及各端点上下文。

额外运行时规则：request_id<=64；idempotency_key<=128；14-17不能mature_non_explicit；must_include/must_avoid不能同项；safety_profile评级须与preferences一致。选中角色绑定完整且唯一，voice.performer_id一致。Revise目标必须存在且范围无歧义，详见[范围表](01-architecture.md)。

### AssetReference

|字段|必填|类型/默认/全部约束|业务含义|
|---|---|---|---|
|`asset_id`|是|`{"type": "string", "maxLength": 256, "minLength": 1}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`revision`|否|`{"type": "string", "maxLength": 64, "minLength": 1, "default": "1"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`uri`|否|`{"anyOf": [{"type": "string"}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`performer_id`|否|`{"anyOf": [{"type": "string"}, {"type": "null"}]}`|演绎者/音色路由ID，与character_id、role_id分离|

额外字段：`false`；false表示拒绝未声明字段。

### AudienceBand

```json
{
  "type": "string",
  "enum": [
    "14-17",
    "18-35",
    "36-55"
  ],
  "title": "AudienceBand"
}
```

### CanonInput

|字段|必填|类型/默认/全部约束|业务含义|
|---|---|---|---|
|`character_id`|否|`{"anyOf": [{"type": "string"}, {"type": "null"}], "description": "长期角色ID（opaque）"}`|长期角色ID（opaque）|
|`name`|是|`{"type": "string", "maxLength": 128, "minLength": 1}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`gender`|否|`{"anyOf": [{"type": "string"}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`age`|否|`{"anyOf": [{"type": "integer", "minimum": 0.0}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`age_band`|否|`{"anyOf": [{"type": "string"}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`species`|否|`{"anyOf": [{"type": "string"}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`character_type`|否|`{"anyOf": [{"type": "string"}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`identity`|否|`{"anyOf": [{"type": "string"}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`world_id`|否|`{"anyOf": [{"type": "string"}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`appearance`|否|`{"anyOf": [{"$ref": "#/components/schemas/CharacterAppearance"}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`abilities`|否|`{"items": {"type": "string"}, "type": "array"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`immutable_facts`|否|`{"items": {"type": "string"}, "type": "array"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|

额外字段：`false`；false表示拒绝未声明字段。

### CharacterAppearance

|字段|必填|类型/默认/全部约束|业务含义|
|---|---|---|---|
|`description`|否|`{"anyOf": [{"type": "string"}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`signature_elements`|否|`{"items": {"type": "string"}, "type": "array"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|

### CharacterBatch

|字段|必填|类型/默认/全部约束|业务含义|
|---|---|---|---|
|`characters`|是|`{"items": {"$ref": "#/components/schemas/CharacterImport"}, "type": "array", "maxItems": 100, "minItems": 1}`|导入批次1..100角色或故事角色选择（依所属模型）|

额外字段：`false`；false表示拒绝未声明字段。

### CharacterImport

|字段|必填|类型/默认/全部约束|业务含义|
|---|---|---|---|
|`character_id`|是|`{"type": "string", "maxLength": 64, "minLength": 1}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`expected_version`|是|`{"type": "integer", "minimum": 0.0}`|当前角色Canon版本号，首次导入0|
|`canon`|是|`{"$ref": "#/components/schemas/CanonInput"}`|固定身份与事实，非空既有值不能覆写|
|`runtime_defaults`|否|`{"$ref": "#/components/schemas/RuntimeInput"}`|公共默认动态参数，不等于调用人个性化版本|
|`assets`|否|`{"additionalProperties": {"$ref": "#/components/schemas/AssetReference"}, "propertyNames": {"enum": ["image", "voice", "expression", "action"]}, "type": "object"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|

额外字段：`false`；false表示拒绝未声明字段。

### CharacterPortrayal

|字段|必填|类型/默认/全部约束|业务含义|
|---|---|---|---|
|`role_id`|是|`{"type": "string", "minLength": 1}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`story_name`|是|`{"type": "string", "minLength": 1}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`external_goal`|是|`{"type": "string", "minLength": 1}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`emotional_avoidance`|是|`{"type": "string", "minLength": 1}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`speech_style`|是|`{"type": "string", "minLength": 1}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`relationship_stance`|是|`{"type": "string", "minLength": 1}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|

额外字段：`false`；false表示拒绝未声明字段。

### CharacterSelection

|字段|必填|类型/默认/全部约束|业务含义|
|---|---|---|---|
|`selected_character_ids`|否|`{"items": {"type": "string"}, "type": "array"}`|角色库标识列表，需配合角色绑定|
|`character_profile_revisions`|否|`{"additionalProperties": {"type": "integer"}, "type": "object"}`|选择调用人Runtime修订|
|`temporary_profile_overrides`|否|`{"additionalProperties": {"additionalProperties": true, "type": "object"}, "type": "object"}`|本次临时Runtime覆盖，不能修改固定Canon|
|`new_fictional_roles`|否|`{"items": {"additionalProperties": true, "type": "object"}, "type": "array"}`|临时虚构角色；已有角色优先经库导入|
|`role_bindings`|否|`{"items": {"additionalProperties": true, "type": "object"}, "type": "array"}`|每角色character_id+role_id或story_role_id+performer_id|

额外字段：`false`；false表示拒绝未声明字段。

### CharacterSnapshot

|字段|必填|类型/默认/全部约束|业务含义|
|---|---|---|---|
|`character_id`|是|`{"type": "string"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`canon_revision`|否|`{"type": "integer", "default": 1}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`profile_revision`|否|`{"type": "integer", "default": 1}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`canon`|否|`{"additionalProperties": true, "type": "object"}`|固定身份与事实，非空既有值不能覆写|
|`profile`|否|`{"additionalProperties": true, "type": "object"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`role_id`|否|`{"anyOf": [{"type": "string"}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`performer_id`|否|`{"anyOf": [{"type": "string"}, {"type": "null"}]}`|演绎者/音色路由ID，与character_id、role_id分离|

额外字段：`false`；false表示拒绝未声明字段。

### CharacterView

|字段|必填|类型/默认/全部约束|业务含义|
|---|---|---|---|
|`character_id`|是|`{"type": "string"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`version`|是|`{"type": "integer"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`canon`|是|`{"$ref": "#/components/schemas/CanonInput"}`|固定身份与事实，非空既有值不能覆写|
|`runtime_defaults`|是|`{"$ref": "#/components/schemas/RuntimeInput"}`|公共默认动态参数，不等于调用人个性化版本|
|`assets`|是|`{"additionalProperties": {"$ref": "#/components/schemas/AssetReference"}, "type": "object"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|

额外字段：`false`；false表示拒绝未声明字段。

### ContentRating

```json
{
  "type": "string",
  "enum": [
    "teen",
    "mature_non_explicit"
  ],
  "title": "ContentRating"
}
```

### ContinueStoryRequest

|字段|必填|类型/默认/全部约束|业务含义|
|---|---|---|---|
|`request_id`|是|`{"type": "string", "minLength": 1}`|客户端逻辑请求标识；HTTP层长度<=64并按principal哈希送入引擎|
|`idempotency_key`|是|`{"type": "string", "minLength": 1}`|同一次动作重放键，长度<=128；新动作换键|
|`session_id`|否|`{"anyOf": [{"type": "string"}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`user_instruction`|是|`{"type": "string", "minLength": 1}`|用户要求，不能为空|
|`creation_preferences`|否|`{"$ref": "#/components/schemas/CreationPreferences"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`characters`|否|`{"$ref": "#/components/schemas/CharacterSelection"}`|导入批次1..100角色或故事角色选择（依所属模型）|
|`template_ref`|否|`{"anyOf": [{"$ref": "#/components/schemas/StoryTemplateRef"}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`safety_profile`|否|`{"anyOf": [{"$ref": "#/components/schemas/SafetyProfile"}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`client_context`|否|`{"additionalProperties": true, "type": "object"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`intent`|否|`{"type": "string", "const": "continue", "default": "continue"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`source_selector`|是|`{"$ref": "#/components/schemas/SourceSelector"}`|指定不可变来源；推荐by_id|
|`continuation_kind`|否|`{"type": "string", "enum": ["next_episode", "resume_playback"], "default": "next_episode"}`|使用next_episode；resume_playback不是正文生成，异步任务将失败|

额外字段：`false`；false表示拒绝未声明字段。

### CreateStoryRequest

|字段|必填|类型/默认/全部约束|业务含义|
|---|---|---|---|
|`request_id`|是|`{"type": "string", "minLength": 1}`|客户端逻辑请求标识；HTTP层长度<=64并按principal哈希送入引擎|
|`idempotency_key`|是|`{"type": "string", "minLength": 1}`|同一次动作重放键，长度<=128；新动作换键|
|`session_id`|否|`{"anyOf": [{"type": "string"}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`user_instruction`|是|`{"type": "string", "minLength": 1}`|用户要求，不能为空|
|`creation_preferences`|否|`{"$ref": "#/components/schemas/CreationPreferences"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`characters`|否|`{"$ref": "#/components/schemas/CharacterSelection"}`|导入批次1..100角色或故事角色选择（依所属模型）|
|`template_ref`|否|`{"anyOf": [{"$ref": "#/components/schemas/StoryTemplateRef"}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`safety_profile`|否|`{"anyOf": [{"$ref": "#/components/schemas/SafetyProfile"}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`client_context`|否|`{"additionalProperties": true, "type": "object"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`intent`|否|`{"type": "string", "const": "create", "default": "create"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`source_selector`|否|`{"type": "null"}`|指定不可变来源；推荐by_id|

额外字段：`false`；false表示拒绝未声明字段。

### CreationPreferences

|字段|必填|类型/默认/全部约束|业务含义|
|---|---|---|---|
|`story_mode`|否|`{"$ref": "#/components/schemas/StoryModeChoice", "default": "auto"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`content_form`|否|`{"type": "string", "const": "audio_drama", "default": "audio_drama"}`|当前HTTP只接受audio_drama|
|`genre`|否|`{"type": "string", "default": "auto"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`recipe_id`|否|`{"anyOf": [{"type": "string"}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`tone_style`|否|`{"type": "string", "default": "warm"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`audience_band`|否|`{"$ref": "#/components/schemas/AudienceBand", "default": "18-35"}`|仅14-17、18-35、36-55；无低龄儿童档|
|`content_rating`|否|`{"$ref": "#/components/schemas/ContentRating", "default": "teen"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`target_duration_sec`|否|`{"type": "integer", "enum": [90, 120, 150, 180, 300], "default": 180}`|每集创作目标，不是实际音频时长保证|
|`target_episodes`|否|`{"type": "integer", "maximum": 100.0, "minimum": 1.0, "default": 1}`|Create/Remix总集数；Continue新增集数|
|`language`|否|`{"type": "string", "default": "zh-CN"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`narration_pov`|否|`{"type": "string", "enum": ["auto", "first", "third"], "default": "auto"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`dialogue_density`|否|`{"type": "string", "enum": ["low", "medium", "high"], "default": "medium"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`pacing`|否|`{"type": "string", "enum": ["slow", "normal", "fast"], "default": "normal"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`ending_preference`|否|`{"type": "string", "enum": ["auto", "happy", "closed", "open", "cliffhanger"], "default": "auto"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`theme_keywords`|否|`{"items": {"type": "string"}, "type": "array"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`must_include`|否|`{"items": {"type": "string"}, "type": "array"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`must_avoid`|否|`{"items": {"type": "string"}, "type": "array"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`additional_instructions`|否|`{"type": "string", "default": ""}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|

额外字段：`false`；false表示拒绝未声明字段。

### EditPolicy

|字段|必填|类型/默认/全部约束|业务含义|
|---|---|---|---|
|`timing`|否|`{"type": "string", "enum": ["pre_playback", "historical"], "default": "historical"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`scope`|是|`{"type": "string", "enum": ["line", "scene", "episode", "arc", "ending", "whole_story"]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`target`|否|`{"additionalProperties": true, "type": "object"}`|按scope解析的集/场景/台词ID，不支持任意播放区间|
|`instruction`|是|`{"type": "string", "minLength": 1}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`preserve`|否|`{"additionalProperties": {"type": "boolean"}, "type": "object"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`downstream_policy`|否|`{"type": "string", "enum": ["replan_affected", "branch_from_here"], "default": "replan_affected"}`|branch_from_here保留后续；replan_affected把受影响后续纳入审批|

额外字段：`false`；false表示拒绝未声明字段。

### Emotion

```json
{
  "type": "string",
  "enum": [
    "neutral",
    "calm",
    "happy",
    "excited",
    "sad",
    "worried",
    "angry",
    "surprised",
    "fearful",
    "reassuring",
    "serious",
    "playful",
    "mysterious",
    "tender"
  ],
  "title": "Emotion"
}
```

### EmphasisSpan

|字段|必填|类型/默认/全部约束|业务含义|
|---|---|---|---|
|`span_text`|是|`{"type": "string", "minLength": 1}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`occurrence`|否|`{"type": "integer", "minimum": 1.0, "default": 1}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`start_char`|否|`{"anyOf": [{"type": "integer", "minimum": 0.0}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`end_char`|否|`{"anyOf": [{"type": "integer", "minimum": 1.0}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`strength`|否|`{"type": "string", "enum": ["medium", "strong"], "default": "medium"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|

额外字段：`false`；false表示拒绝未声明字段。

### EpisodeDelivery

|字段|必填|类型/默认/全部约束|业务含义|
|---|---|---|---|
|`episode_id`|是|`{"type": "string"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`index`|是|`{"type": "integer"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`title`|是|`{"type": "string"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`synopsis`|否|`{"type": "string", "default": ""}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`duration_target_sec`|是|`{"type": "integer"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`duration_estimate_sec`|否|`{"type": "integer", "default": 0}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`scenes`|否|`{"items": {"$ref": "#/components/schemas/SceneDelivery"}, "type": "array"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`episode_summary`|否|`{"type": "string", "default": ""}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`ending_hook`|否|`{"type": "string", "default": ""}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`fact_delta`|否|`{"items": {"additionalProperties": true, "type": "object"}, "type": "array"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`relationship_delta`|否|`{"items": {"additionalProperties": true, "type": "object"}, "type": "array"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`new_open_threads`|否|`{"items": {"type": "string"}, "type": "array"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`closed_threads`|否|`{"items": {"type": "string"}, "type": "array"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`validation_status`|否|`{"type": "string", "enum": ["PENDING", "PASSED", "FAILED"], "default": "PENDING"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`ready_for_playback`|否|`{"type": "boolean", "default": false}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|

额外字段：`false`；false表示拒绝未声明字段。

### EpisodeOutline

|字段|必填|类型/默认/全部约束|业务含义|
|---|---|---|---|
|`index`|是|`{"type": "integer", "minimum": 1.0}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`title`|是|`{"type": "string"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`core_goal`|是|`{"type": "string"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`major_beats`|是|`{"items": {"type": "string"}, "type": "array", "minItems": 1}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`ending`|是|`{"type": "string"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`target_duration_sec`|是|`{"type": "integer"}`|每集创作目标，不是实际音频时长保证|

额外字段：`false`；false表示拒绝未声明字段。

### ErrorInfo

|字段|必填|类型/默认/全部约束|业务含义|
|---|---|---|---|
|`code`|是|`{"type": "string"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`message`|是|`{"type": "string"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`request_id`|是|`{"type": "string"}`|客户端逻辑请求标识；HTTP层长度<=64并按principal哈希送入引擎|
|`details`|否|`{"items": {"additionalProperties": true, "type": "object"}, "type": "array"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|

额外字段：`false`；false表示拒绝未声明字段。

### ErrorResponse

|字段|必填|类型/默认/全部约束|业务含义|
|---|---|---|---|
|`error`|是|`{"$ref": "#/components/schemas/ErrorInfo"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|

额外字段：`false`；false表示拒绝未声明字段。

### FictionalVoiceRole

|字段|必填|类型/默认/全部约束|业务含义|
|---|---|---|---|
|`role_id`|是|`{"type": "string", "minLength": 1}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`story_name`|是|`{"type": "string", "minLength": 1}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`performer_id`|是|`{"type": "string", "minLength": 1}`|演绎者/音色路由ID，与character_id、role_id分离|
|`medium`|是|`{"type": "string", "enum": ["live", "recording", "telephone", "broadcast"]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`dramatic_purpose`|是|`{"type": "string", "minLength": 1}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|

额外字段：`false`；false表示拒绝未声明字段。

### JobView

|字段|必填|类型/默认/全部约束|业务含义|
|---|---|---|---|
|`job_id`|是|`{"type": "string"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`request_id`|是|`{"type": "string"}`|客户端逻辑请求标识；HTTP层长度<=64并按principal哈希送入引擎|
|`status`|是|`{"type": "string"}`|所属资源状态；详见状态图，不同资源不能混用|
|`stage`|是|`{"type": "string"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`progress_pct`|是|`{"type": "integer"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`approved_plan_id`|是|`{"type": "string"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`approved_plan_revision`|是|`{"type": "integer"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`result_story_version_id`|否|`{"anyOf": [{"type": "string"}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`error_code`|否|`{"anyOf": [{"type": "string"}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`error_message`|否|`{"anyOf": [{"type": "string"}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`usage`|否|`{"additionalProperties": true, "type": "object"}`|累计调用记录；按call_id去重，reasoning可能已含于output|
|`story_state`|是|`{"type": "string", "enum": ["NOT_GENERATED", "GENERATING", "SUCCEEDED", "FAILED", "CANCELLED"]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`audio_state`|否|`{"type": "string", "const": "NOT_SYNTHESIZED", "default": "NOT_SYNTHESIZED"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`playback_state`|否|`{"type": "string", "const": "NOT_REPORTED", "default": "NOT_REPORTED"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|

额外字段：`false`；false表示拒绝未声明字段。

### MysteryCausalProof

|字段|必填|类型/默认/全部约束|业务含义|
|---|---|---|---|
|`actual_event_timeline`|是|`{"type": "string", "minLength": 8}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`witnessed_event`|是|`{"type": "string", "minLength": 8}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`actor_opportunity`|是|`{"type": "string", "minLength": 8}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`audible_trace_before_reveal`|是|`{"type": "string", "minLength": 8}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`onstage_verification`|是|`{"type": "string", "minLength": 8}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|

额外字段：`false`；false表示拒绝未声明字段。

### PauseCue

|字段|必填|类型/默认/全部约束|业务含义|
|---|---|---|---|
|`after_char`|是|`{"type": "integer", "minimum": 0.0}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`duration_ms`|是|`{"type": "integer", "maximum": 5000.0, "minimum": 0.0}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|

额外字段：`false`；false表示拒绝未声明字段。

### PerformanceValidationReport

|字段|必填|类型/默认/全部约束|业务含义|
|---|---|---|---|
|`validation_status`|是|`{"type": "string", "enum": ["PASSED", "FAILED"]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`spoken_line_count`|否|`{"type": "integer", "default": 0}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`emotion_coverage`|否|`{"type": "number", "default": 0.0}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`tone_coverage`|否|`{"type": "number", "default": 0.0}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`emphasis_coverage`|否|`{"type": "number", "default": 0.0}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`emphasis_span_validity`|否|`{"type": "number", "default": 0.0}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`continuity_passed`|否|`{"type": "boolean", "default": true}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`outline_alignment_passed`|否|`{"type": "boolean", "default": true}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`content_rating_passed`|否|`{"type": "boolean", "default": true}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`warnings`|否|`{"items": {"type": "string"}, "type": "array"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`operation_checks`|否|`{"items": {"additionalProperties": true, "type": "object"}, "type": "array"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`timeline_claims`|否|`{"items": {"additionalProperties": true, "type": "object"}, "type": "array"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|

额外字段：`false`；false表示拒绝未声明字段。

### PlanApprovalRequest

|字段|必填|类型/默认/全部约束|业务含义|
|---|---|---|---|
|`expected_revision`|是|`{"type": "integer", "minimum": 1.0}`|客户端已读取的当前修订号，用于并发保护|
|`fingerprint`|是|`{"type": "string", "maxLength": 64, "minLength": 64}`|刚读到的plan_fingerprint，精确64字符|
|`idempotency_key`|是|`{"type": "string", "maxLength": 128, "minLength": 1}`|同一次动作重放键，长度<=128；新动作换键|

额外字段：`false`；false表示拒绝未声明字段。

### PlanContent

|字段|必填|类型/默认/全部约束|业务含义|
|---|---|---|---|
|`title`|是|`{"type": "string"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`premise`|是|`{"type": "string"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`theme`|是|`{"type": "string"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`beginning`|是|`{"type": "string"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`development`|是|`{"type": "string"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`climax`|是|`{"type": "string"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`ending`|是|`{"type": "string"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`episode_outlines`|是|`{"items": {"$ref": "#/components/schemas/EpisodeOutline"}, "type": "array", "minItems": 1}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`character_portrayals`|否|`{"items": {"$ref": "#/components/schemas/CharacterPortrayal"}, "type": "array"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`fictional_voice_roles`|否|`{"items": {"$ref": "#/components/schemas/FictionalVoiceRole"}, "type": "array"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`mystery_causal_proof`|否|`{"anyOf": [{"$ref": "#/components/schemas/MysteryCausalProof"}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`performance_plan`|否|`{"additionalProperties": true, "type": "object"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`continuity_constraints`|否|`{"items": {"type": "string"}, "type": "array"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`change_summary`|否|`{"items": {"type": "string"}, "type": "array"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|

额外字段：`false`；false表示拒绝未声明字段。

### PlanPreview

|字段|必填|类型/默认/全部约束|业务含义|
|---|---|---|---|
|`plan_id`|是|`{"type": "string"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`plan_revision`|是|`{"type": "integer", "minimum": 1.0}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`status`|是|`{"$ref": "#/components/schemas/PlanStatus"}`|所属资源状态；详见状态图，不同资源不能混用|
|`intent`|是|`{"$ref": "#/components/schemas/StoryIntent"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`requires_confirmation`|否|`{"type": "boolean", "default": true}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`source_reference`|否|`{"anyOf": [{"$ref": "#/components/schemas/ResolvedSourceReference"}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`template_snapshot`|否|`{"anyOf": [{"additionalProperties": true, "type": "object"}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`strategy_snapshot`|否|`{"anyOf": [{"additionalProperties": true, "type": "object"}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`plan`|是|`{"$ref": "#/components/schemas/PlanContent"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`resolved_preferences`|是|`{"$ref": "#/components/schemas/CreationPreferences"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`character_snapshots`|否|`{"items": {"$ref": "#/components/schemas/CharacterSnapshot"}, "type": "array"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`character_profile_revision_map`|否|`{"additionalProperties": {"type": "integer"}, "type": "object"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`auto_filled_fields`|否|`{"items": {"type": "string"}, "type": "array"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`inherited_fields`|否|`{"items": {"type": "string"}, "type": "array"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`impact_analysis`|否|`{"additionalProperties": true, "type": "object"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`model_trace`|否|`{"items": {"additionalProperties": true, "type": "object"}, "type": "array"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`rule_versions`|否|`{"additionalProperties": {"type": "string"}, "type": "object"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`explicit_fields`|否|`{"items": {"type": "string"}, "type": "array"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`plan_fingerprint`|是|`{"type": "string"}`|审批快照校验值；传给审批body.fingerprint|
|`allowed_actions`|否|`{"items": {"type": "string"}, "type": "array"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`created_at`|否|`{"type": "string", "format": "date-time"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`updated_at`|否|`{"type": "string", "format": "date-time"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|

额外字段：`false`；false表示拒绝未声明字段。

### PlanRevisionRequest

|字段|必填|类型/默认/全部约束|业务含义|
|---|---|---|---|
|`expected_revision`|是|`{"type": "integer", "minimum": 1.0}`|客户端已读取的当前修订号，用于并发保护|
|`feedback`|是|`{"type": "string", "minLength": 1}`|未审批大纲的新反馈，不修改已发布正文|
|`idempotency_key`|是|`{"type": "string", "maxLength": 128, "minLength": 1}`|同一次动作重放键，长度<=128；新动作换键|

额外字段：`false`；false表示拒绝未声明字段。

### PlanStatus

```json
{
  "type": "string",
  "enum": [
    "PREPARING",
    "AWAITING_APPROVAL",
    "REVISING",
    "APPROVED",
    "STALE",
    "CANCELLED",
    "EXPIRED"
  ],
  "title": "PlanStatus"
}
```

### RemixPolicy

|字段|必填|类型/默认/全部约束|业务含义|
|---|---|---|---|
|`instruction`|是|`{"type": "string", "minLength": 1}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`retain_elements`|是|`{"items": {"type": "string"}, "type": "array", "minItems": 1}`|逐项保留约束，至少1项|
|`transform_elements`|是|`{"items": {"type": "string"}, "type": "array", "minItems": 1}`|逐项变更要求，至少1项|
|`relation_to_source`|否|`{"type": "string", "enum": ["alternate_version", "adaptation", "parallel_world", "new_story"], "default": "alternate_version"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|

额外字段：`false`；false表示拒绝未声明字段。

### RemixStoryRequest

|字段|必填|类型/默认/全部约束|业务含义|
|---|---|---|---|
|`request_id`|是|`{"type": "string", "minLength": 1}`|客户端逻辑请求标识；HTTP层长度<=64并按principal哈希送入引擎|
|`idempotency_key`|是|`{"type": "string", "minLength": 1}`|同一次动作重放键，长度<=128；新动作换键|
|`session_id`|否|`{"anyOf": [{"type": "string"}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`user_instruction`|是|`{"type": "string", "minLength": 1}`|用户要求，不能为空|
|`creation_preferences`|否|`{"$ref": "#/components/schemas/CreationPreferences"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`characters`|否|`{"$ref": "#/components/schemas/CharacterSelection"}`|导入批次1..100角色或故事角色选择（依所属模型）|
|`template_ref`|否|`{"anyOf": [{"$ref": "#/components/schemas/StoryTemplateRef"}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`safety_profile`|否|`{"anyOf": [{"$ref": "#/components/schemas/SafetyProfile"}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`client_context`|否|`{"additionalProperties": true, "type": "object"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`intent`|否|`{"type": "string", "const": "remix", "default": "remix"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`source_selector`|是|`{"$ref": "#/components/schemas/SourceSelector"}`|指定不可变来源；推荐by_id|
|`remix`|是|`{"$ref": "#/components/schemas/RemixPolicy"}`|保留与变更约束|

额外字段：`false`；false表示拒绝未声明字段。

### RenderManifest

|字段|必填|类型/默认/全部约束|业务含义|
|---|---|---|---|
|`contract_version`|否|`{"type": "string", "const": "1.0", "default": "1.0"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`delivery_format_version`|是|`{"type": "string"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`story_version_id`|是|`{"type": "string"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`story_state`|否|`{"type": "string", "const": "SUCCEEDED", "default": "SUCCEEDED"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`audio_state`|否|`{"type": "string", "const": "NOT_SYNTHESIZED", "default": "NOT_SYNTHESIZED"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`playback_state`|否|`{"type": "string", "const": "NOT_REPORTED", "default": "NOT_REPORTED"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`role_map`|是|`{"additionalProperties": true, "type": "object"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`tasks`|是|`{"items": {"$ref": "#/components/schemas/RenderTask"}, "type": "array"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|

额外字段：`false`；false表示拒绝未声明字段。

### RenderTask

|字段|必填|类型/默认/全部约束|业务含义|
|---|---|---|---|
|`task_id`|是|`{"type": "string"}`|TaskView中为大纲队列ID；RenderTask中为演绎位置哈希，两个命名空间|
|`sequence`|是|`{"type": "integer", "minimum": 1.0}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`episode_id`|是|`{"type": "string"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`episode_index`|是|`{"type": "integer"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`scene_id`|是|`{"type": "string"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`line_id`|是|`{"type": "string"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`kind`|是|`{"type": "string", "enum": ["speech", "cue"]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`source_kind`|是|`{"type": "string", "enum": ["dialogue", "narration", "sfx", "music", "action"]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`speaker_role_id`|否|`{"anyOf": [{"type": "string"}, {"type": "null"}]}`|本故事内说话角色ID|
|`narrator_id`|否|`{"anyOf": [{"type": "string"}, {"type": "null"}]}`|旁白标识；不冒充角色role_id|
|`performer_id`|否|`{"anyOf": [{"type": "string"}, {"type": "null"}]}`|演绎者/音色路由ID，与character_id、role_id分离|
|`text`|是|`{"type": "string"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`performance`|是|`{"additionalProperties": true, "type": "object"}`|从Utterance保留的演绎参数；客户端按能力映射|
|`asset_refs`|否|`{"additionalProperties": true, "type": "object"}`|资源引用，无自动下载/合成|

额外字段：`false`；false表示拒绝未声明字段。

### ResolvedSourceReference

|字段|必填|类型/默认/全部约束|业务含义|
|---|---|---|---|
|`story_id`|是|`{"type": "string"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`story_version_id`|是|`{"type": "string"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`version_no`|否|`{"type": "integer", "default": 1}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`episode_id`|否|`{"anyOf": [{"type": "string"}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`series_id`|否|`{"anyOf": [{"type": "string"}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`resolved_reason`|是|`{"type": "string"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`last_played_position_ms`|否|`{"anyOf": [{"type": "integer"}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`story_summary`|否|`{"type": "string", "default": ""}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`continuity_snapshot`|否|`{"additionalProperties": true, "type": "object"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`source_content_snapshot`|否|`{"additionalProperties": true, "type": "object"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|

额外字段：`false`；false表示拒绝未声明字段。

### ReviseStoryRequest

|字段|必填|类型/默认/全部约束|业务含义|
|---|---|---|---|
|`request_id`|是|`{"type": "string", "minLength": 1}`|客户端逻辑请求标识；HTTP层长度<=64并按principal哈希送入引擎|
|`idempotency_key`|是|`{"type": "string", "minLength": 1}`|同一次动作重放键，长度<=128；新动作换键|
|`session_id`|否|`{"anyOf": [{"type": "string"}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`user_instruction`|是|`{"type": "string", "minLength": 1}`|用户要求，不能为空|
|`creation_preferences`|否|`{"$ref": "#/components/schemas/CreationPreferences"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`characters`|否|`{"$ref": "#/components/schemas/CharacterSelection"}`|导入批次1..100角色或故事角色选择（依所属模型）|
|`template_ref`|否|`{"anyOf": [{"$ref": "#/components/schemas/StoryTemplateRef"}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`safety_profile`|否|`{"anyOf": [{"$ref": "#/components/schemas/SafetyProfile"}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`client_context`|否|`{"additionalProperties": true, "type": "object"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`intent`|否|`{"type": "string", "const": "revise", "default": "revise"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`source_selector`|是|`{"$ref": "#/components/schemas/SourceSelector"}`|指定不可变来源；推荐by_id|
|`edit`|是|`{"$ref": "#/components/schemas/EditPolicy"}`|正文局部修改范围；不是Plan feedback|

额外字段：`false`；false表示拒绝未声明字段。

### RuntimeInput

|字段|必填|类型/默认/全部约束|业务含义|
|---|---|---|---|
|`personality`|否|`{"anyOf": [{"items": {"type": "string"}, "type": "array"}, {"type": "null"}], "description": "性格标签"}`|性格标签|
|`goal`|否|`{"anyOf": [{"type": "string"}, {"type": "null"}], "description": "当前目标/欲望"}`|当前目标/欲望|
|`motivation`|否|`{"anyOf": [{"type": "string"}, {"type": "null"}], "description": "行为动机"}`|行为动机|
|`fear`|否|`{"anyOf": [{"type": "string"}, {"type": "null"}], "description": "最怕失去的事物"}`|最怕失去的事物|
|`stance`|否|`{"anyOf": [{"type": "string"}, {"type": "null"}], "description": "对核心冲突的立场"}`|对核心冲突的立场|
|`dialogue_style`|否|`{"anyOf": [{"type": "string"}, {"type": "null"}], "description": "说话风格"}`|说话风格|
|`story_function`|否|`{"anyOf": [{"type": "string"}, {"type": "null"}], "description": "叙事功能角色"}`|叙事功能角色|

额外字段：`false`；false表示拒绝未声明字段。

### RuntimeUpdate

|字段|必填|类型/默认/全部约束|业务含义|
|---|---|---|---|
|`expected_revision`|是|`{"type": "integer", "minimum": 0.0}`|客户端已读取的当前修订号，用于并发保护|
|`patch`|是|`{"$ref": "#/components/schemas/RuntimeInput"}`|仅Runtime字段；省略字段不改，明确null的语义按模型处理|

额外字段：`false`；false表示拒绝未声明字段。

### RuntimeView

|字段|必填|类型/默认/全部约束|业务含义|
|---|---|---|---|
|`character_id`|是|`{"type": "string"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`revision`|是|`{"type": "integer"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`profile`|是|`{"additionalProperties": true, "type": "object"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|

额外字段：`false`；false表示拒绝未声明字段。

### SafetyProfile

|字段|必填|类型/默认/全部约束|业务含义|
|---|---|---|---|
|`content_rating`|否|`{"$ref": "#/components/schemas/ContentRating", "default": "teen"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`blocked_topics`|否|`{"items": {"type": "string"}, "type": "array"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|

额外字段：`false`；false表示拒绝未声明字段。

### SceneDelivery

|字段|必填|类型/默认/全部约束|业务含义|
|---|---|---|---|
|`scene_id`|是|`{"type": "string"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`scene_index`|否|`{"type": "integer", "minimum": 1.0, "description": "1-based scene index within the episode", "default": 1}`|1-based scene index within the episode|
|`title`|是|`{"type": "string"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`location`|否|`{"type": "string", "default": ""}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`dramatic_goal`|否|`{"type": "string", "default": ""}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`utterances`|否|`{"items": {"$ref": "#/components/schemas/Utterance"}, "type": "array"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|

额外字段：`false`；false表示拒绝未声明字段。

### SourceSelector

|字段|必填|类型/默认/全部约束|业务含义|
|---|---|---|---|
|`type`|是|`{"type": "string", "enum": ["last_generated", "last_played", "series_latest", "by_id"]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`series_id`|否|`{"anyOf": [{"type": "string"}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`story_id`|否|`{"anyOf": [{"type": "string"}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`story_version_id`|否|`{"anyOf": [{"type": "string"}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`episode_id`|否|`{"anyOf": [{"type": "string"}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`playback_position_ms`|否|`{"anyOf": [{"type": "integer", "minimum": 0.0}, {"type": "null"}]}`|来源参考元数据，不实现毫秒截断、续播或保存进度|

额外字段：`false`；false表示拒绝未声明字段。

### StoryDelivery

|字段|必填|类型/默认/全部约束|业务含义|
|---|---|---|---|
|`story_id`|是|`{"type": "string"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`story_version_id`|是|`{"type": "string"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`version_no`|否|`{"type": "integer", "default": 1}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`parent_story_version_id`|否|`{"anyOf": [{"type": "string"}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`lineage_type`|否|`{"type": "string", "enum": ["create", "continue", "revise", "remix"], "default": "create"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`series_id`|否|`{"anyOf": [{"type": "string"}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`approved_plan_id`|是|`{"type": "string"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`approved_plan_revision`|是|`{"type": "integer"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`title`|是|`{"type": "string"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`summary`|是|`{"type": "string"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`preview_blurb`|否|`{"type": "string", "default": ""}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`tags`|否|`{"items": {"type": "string"}, "type": "array"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`story_mode`|是|`{"type": "string"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`content_form`|否|`{"type": "string", "const": "audio_drama", "default": "audio_drama"}`|当前HTTP只接受audio_drama|
|`character_snapshot`|否|`{"items": {"$ref": "#/components/schemas/CharacterSnapshot"}, "type": "array"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`character_portrayals`|否|`{"items": {"additionalProperties": true, "type": "object"}, "type": "array", "description": "Plan-generated character portrayals: role_id→story_name, goal, avoidance, speech_style, relationship_stance"}`|Plan-generated character portrayals: role_id→story_name, goal, avoidance, speech_style, relationship_stance|
|`fictional_voice_roles`|否|`{"items": {"additionalProperties": true, "type": "object"}, "type": "array", "description": "Non-toy voice roles from Plan: recording, broadcast, telephone voices with role_id, story_name, performer_id, medium, dramatic_purpose"}`|Non-toy voice roles from Plan: recording, broadcast, telephone voices with role_id, story_name, performer_id, medium, dramatic_purpose|
|`outline`|是|`{"$ref": "#/components/schemas/PlanContent"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`episodes`|否|`{"items": {"$ref": "#/components/schemas/EpisodeDelivery"}, "type": "array"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`generated_episode_ids`|否|`{"items": {"type": "string"}, "type": "array"}`|本次生成集；完整episodes可含继承集|
|`continuity_state`|否|`{"additionalProperties": true, "type": "object"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`quality_report`|是|`{"$ref": "#/components/schemas/PerformanceValidationReport"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`ready_for_playback`|否|`{"type": "boolean", "default": false}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`status`|否|`{"$ref": "#/components/schemas/StoryVersionStatus", "default": "CANDIDATE"}`|所属资源状态；详见状态图，不同资源不能混用|
|`engine_version`|否|`{"type": "string", "default": "0.3.0"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`delivery_format_version`|否|`{"type": "string", "description": "Format version for downstream compatibility; increment when field semantics change", "default": "1.0"}`|Format version for downstream compatibility; increment when field semantics change|
|`rule_versions`|否|`{"additionalProperties": true, "type": "object"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`model_trace`|否|`{"items": {"additionalProperties": true, "type": "object"}, "type": "array"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`created_at`|否|`{"type": "string", "format": "date-time"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|

额外字段：`false`；false表示拒绝未声明字段。

### StoryIntent

```json
{
  "type": "string",
  "enum": [
    "create",
    "continue",
    "revise",
    "remix"
  ],
  "title": "StoryIntent"
}
```

### StoryModeChoice

```json
{
  "type": "string",
  "enum": [
    "auto",
    "general",
    "mystery",
    "serialized",
    "viral_drama"
  ],
  "title": "StoryModeChoice"
}
```

### StoryTemplateRef

|字段|必填|类型/默认/全部约束|业务含义|
|---|---|---|---|
|`template_id`|是|`{"type": "string"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`template_revision`|是|`{"type": "integer", "minimum": 1.0}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|

额外字段：`false`；false表示拒绝未声明字段。

### StoryVersionStatus

```json
{
  "type": "string",
  "enum": [
    "CANDIDATE",
    "READY",
    "SUPERSEDED",
    "ARCHIVED"
  ],
  "title": "StoryVersionStatus"
}
```

### TaskView

|字段|必填|类型/默认/全部约束|业务含义|
|---|---|---|---|
|`task_id`|是|`{"type": "string"}`|TaskView中为大纲队列ID；RenderTask中为演绎位置哈希，两个命名空间|
|`kind`|是|`{"type": "string", "enum": ["prepare", "revise"]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`status`|是|`{"type": "string", "enum": ["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCEL_REQUESTED", "CANCELLED"]}`|所属资源状态；详见状态图，不同资源不能混用|
|`progress_pct`|是|`{"type": "integer"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`stage`|是|`{"type": "string"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`result`|是|`{"additionalProperties": true, "type": "object"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`error_code`|否|`{"anyOf": [{"type": "string"}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`error_message`|否|`{"anyOf": [{"type": "string"}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`usage`|否|`{"additionalProperties": true, "type": "object"}`|累计调用记录；按call_id去重，reasoning可能已含于output|
|`created_at`|是|`{"type": "string", "format": "date-time"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`updated_at`|是|`{"type": "string", "format": "date-time"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|

额外字段：`false`；false表示拒绝未声明字段。

### Utterance

|字段|必填|类型/默认/全部约束|业务含义|
|---|---|---|---|
|`line_id`|是|`{"type": "string"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`kind`|是|`{"type": "string", "enum": ["dialogue", "narration", "sfx", "music", "action"]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`speaker_role_id`|否|`{"anyOf": [{"type": "string"}, {"type": "null"}]}`|本故事内说话角色ID|
|`narrator_id`|否|`{"anyOf": [{"type": "string"}, {"type": "null"}]}`|旁白标识；不冒充角色role_id|
|`performer_id`|否|`{"anyOf": [{"type": "string"}, {"type": "null"}]}`|演绎者/音色路由ID，与character_id、role_id分离|
|`text`|否|`{"type": "string", "default": ""}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`emotion`|否|`{"anyOf": [{"$ref": "#/components/schemas/Emotion"}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`tone_instruction`|否|`{"anyOf": [{"type": "string"}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`emphasis`|否|`{"items": {"$ref": "#/components/schemas/EmphasisSpan"}, "type": "array"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`speech_rate`|否|`{"type": "number", "maximum": 2.0, "minimum": 0.5, "default": 1.0}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`pitch`|否|`{"anyOf": [{"type": "string"}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`volume`|否|`{"anyOf": [{"type": "string"}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`pauses`|否|`{"items": {"$ref": "#/components/schemas/PauseCue"}, "type": "array"}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`delivery_note`|否|`{"anyOf": [{"type": "string"}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|
|`audio_cue_ref`|否|`{"anyOf": [{"type": "string"}, {"type": "null"}]}`|按字段名和所属模型表达的结构化数据；完整值见真实响应|

额外字段：`false`；false表示拒绝未声明字段。
