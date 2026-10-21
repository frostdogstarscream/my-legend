# Phase 11 服务部署与接口约定

基于已验收 `cb93e8b`。复用 StoryWorkflowService、版本仓库、角色 Canon/Runtime 与 StoryDelivery 1.0；API 不重新生成或改写故事。

## 启动

Python 3.11、PostgreSQL 16；仓库根目录安装 `python -m pip install -e '.[service,test]'`。Docker PostgreSQL 使用已有 compose：先在环境设置 `POSTGRES_PASSWORD`，再执行 `docker compose up -d postgres`（服务名以项目 compose 为准）。不要将密钥写入版本控制。

必须通过环境/部署密钥系统设置：

|变量|用途|
|---|---|
|STORY_DATABASE_URL|SQLAlchemy PostgreSQL psycopg 连接串|
|STORY_API_KEYS|JSON 映射：Bearer 密钥 → `{ "principal_id": "业务身份", "admin": true/false }`|
|DRAMA_LLM_API_KEY 或 STORY_LLM_API_KEY|现有真实模型网关凭据|
|STORY_DATABASE_SCHEMA|可选隔离 schema，迁移命令会创建|
|STORY_EVIDENCE_DIR|可选模型原始证据目录，限制文件访问权限|
|STORY_WORKER_LEASE_SECONDS|默认 120|
|STORY_WORKER_HEARTBEAT_SECONDS|默认 10，必须小于租约一半|
|STORY_WORKER_POLL_SECONDS|默认 1|

Provider 的 endpoint/model 等设置沿用现有 `drama_engine.llm.openai_compat` 环境约定；服务不会自动使用 Mock。

```sh
python -m drama_engine.service migrate
python -m drama_engine.service serve --host 127.0.0.1 --port 8000
# 另一进程/容器，使用相同数据库、schema 和模型配置：
python -m drama_engine.service worker
python -m drama_engine.service openapi --output docs/phase11/openapi.json
```

API `/docs` 与 `/openapi.json` 提供交互文档；`/health` 检查数据库。迁移版本必须是 004。生产外网入口应使用 TLS 反向代理；Bearer 密钥按调用方分配，角色导入只允许 admin，故事、Plan、任务与 Runtime 按 principal 隔离。当前提供服务间认证，App 用户登录由上层网关映射 principal；不要把 admin 密钥直接放入 App。

API 与 Worker 可独立部署。Worker 串行执行自身任务，多个进程通过 PostgreSQL `FOR UPDATE SKIP LOCKED` 领取；数据库队列是唯一运行依据。建议 supervisor 自动重启进程。数据库不可用时不能发布版本，失去租约的执行者受到 token fencing 阻止写入。

容器构建：`docker build -f Dockerfile.service -t storygenerate-service .`。镜像包含引擎、规则库与迁移；以 `-e STORY_DATABASE_URL -e STORY_API_KEYS -e STORY_DATABASE_SCHEMA` 从宿主环境传递配置，模型 Worker 另外传递 Provider 的环境变量。连接串主机必须能从容器网络访问 PostgreSQL。首次用镜像运行 `python -m drama_engine.service migrate`，再分别运行默认 API 命令与 `python -m drama_engine.service worker`，不要将数据库迁移并发放在每个 API 副本启动流程中。

## 调用顺序

1. admin `POST /v1/characters/import` 导入 `examples/phase11/characters.json`。
2. `POST /v1/plans` 提交四操作请求，202 返回大纲任务；轮询 `GET /v1/tasks/{task_id}`。
3. 成功后 `GET /v1/plans/{plan_id}`，可 `POST /v1/plans/{plan_id}/revisions`，携带 expected_revision、feedback、idempotency_key，202 返回修订任务。
4. `POST /v1/plans/{plan_id}/approve`，提交最新 expected_revision、fingerprint、idempotency_key；202 返回已持久化 QUEUED 生成任务。审批和入队在同一事务完成。
5. `GET /v1/jobs/{job_id}` 查询状态；SUCCEEDED 后获取 result_story_version_id。
6. `GET /v1/story-versions/{id}` 及 `/export?format=json|script|characters`；`/lineage` 查询续写和二创关系。`GET /v1/stories/{story_id}/versions` 查询不可变历史版本。

未经审批 `POST /v1/plans/{id}/generate` 返回 409。该接口只查询审批时创建的任务，不产生第二个任务。详细、可直接执行的四操作请求见验收入口 `tests/acceptance/phase11_http_live.py`；每次实测目录的 HTTP request/response JSON 包含完整示例（没有 Authorization 头）。

统一错误为 `{ "error": { "code": "...", "message": "...", "request_id": "..." } }`（具体字段见 OpenAPI）；响应 `X-Request-ID` 关联请求。401 未认证，403 admin 权限不足，404 不存在或无访问权，409 版本/审批/幂等冲突，422 输入不合法，503 内部服务不可用。不向客户端回显请求密钥、连接串或堆栈。

## 取消、重试与恢复

大纲：QUEUED → RUNNING → SUCCEEDED/FAILED；生成：QUEUED → RUNNING → VALIDATING → SUCCEEDED/FAILED。排队取消直接 CANCELLED；运行取消进入 CANCEL_REQUESTED，当前模型请求可能继续产生费用，随后禁止下一次模型调用并禁止发布结果。已成功的任务不可重试或取消。

`POST /v1/tasks/{id}/cancel|retry`、`POST /v1/jobs/{id}/cancel|retry`。只有 FAILED 可以显式 retry，CANCELLED 不可重试；累计调用与 attempt_history 保留失败记录。重试使用原审批快照，不更新历史版本。进程中断后 Worker 以数据库 wall clock 检测过期租约，将任务标记 FAILED（或 CANCELLED），注明未知在途用量。必须显式 retry；不会悄悄重复收费调用。

相同 principal 的幂等键与相同请求返回同一资源，不同请求返回冲突。审批校验最新 revision/fingerprint 和显式选中的角色版本。备份必须包含整个 schema；恢复后先 migrate，再启动 API/Worker。进程内缓存不是恢复前提。

## 角色版本

导入携带 expected_version；首次为 0。固定 Canon 事实不能覆写；新资产或 Runtime 默认参数可产生新版本。`PATCH /v1/characters/{id}/runtime` 使用 expected_revision 保存调用方的动态配置；查询 runtime 和版本历史见 OpenAPI。绑定必须同时提供 character_id、role_id/story_role_id、performer_id；语音资源 performer_id 必须一致。准备大纲时冻结角色、Runtime、资产引用，之后库更新不会改写已保存故事。

## 回归与验收

```sh
# TEST_DATABASE_URL 指向测试数据库，测试用独立 schema
python -m pytest -q
# 真实 HTTP、独立 API/Worker、PostgreSQL 和真实模型；schema 与失败证据保留
python tests/acceptance/phase11_http_live.py --output 6-实测产物/phase11_live_NEW
```

单元/工程测试替身仅验证机制；真实验收入口没有 Mock 回退。统计按 call_id 去重，reasoning 已包含在 output，缓存单列；超时或进程中断没有返回用量时标记 unknown，不按零消耗处理。

真实验收完成并停止 Worker 后，可执行 `python tests/acceptance/phase11_database_restore.py --manifest <实测目录>/manifest.json --output <实测目录>/database_restore.json`。脚本在项目 PostgreSQL 容器内使用 pg_dump/pg_restore 创建独立恢复库，并比较全部角色、Plan、任务、版本行的哈希；保留恢复库与容器内备份。需要 Docker CLI，数据库连接须与项目容器对应。备份不纳入 Git。

本批次失败与修复见 [最终验收报告](ACCEPTANCE.md)。`phase11_http_live.py --output <新目录> --resume-remix-from <原目录>` 可从已通过前三阶段的同一数据库重新读取验证，并通过正式 retry API 重新执行失败 Remix；历史证据复制到新目录，原目录保持不变。跨目录统计必须使用 `phase11_statistics.py` 按物理 call_id 去重，不能直接累加各目录 Token。
