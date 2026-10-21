# Phase 11 服务化与业务闭环验收报告

日期：2026-10-15（Asia/Shanghai）；原始模型与数据库时间戳保留 UTC。

## 结论与交付边界

四操作通过 HTTP、独立 Worker、PostgreSQL、重新读取、完整导出及模拟 TTS Consumer 闭环。交付范围为服务后端、角色数据接入、结构化故事与演绎任务。音频状态保持 NOT_SYNTHESIZED，设备状态保持 NOT_REPORTED。

|功能|已实现|工程测试通过|真实模型通过|本轮后端交付条件|
|---|---|---|---|---|
|[Create](CREATE.md)|是|是|是|满足|
|[Revise](REVISE.md)|是|是|是|满足|
|[Continue](CONTINUE.md)|是|是|是|满足|
|[Remix](REMIX.md)|是|是|是|满足|

基线 `cb93e8be604e7b58f5b02911d11303e6895efb53`，最终行为修复提交 `9517b0d6d8ea8b3cbee7d3a34dc023ff83a857b1`。Create/Revise/Continue 的真实模型生成来自 `84119ea`，随后在 9517b0d 中重新通过 HTTP 读取、逐字段比较及新时间校验器复验；Remix 在 9517b0d 中从同一个已通过 Continue 版本真实重试生成。版本谱系始终在同一个 PostgreSQL schema 中；该增量复验没有再次调用前三项模型，没有人工修改正文。

[最终证据目录](../../6-实测产物/phase11_live_20261015_v3/)、[逐句来源及语义引用校验](../../6-实测产物/phase11_live_20261015_v3/evidence_review.json)、[旧三阶段复验](../../6-实测产物/phase11_live_20261015_v3/prior_stage_revalidation.json)。v3 复制的历史证据用于完整追溯，统计按全局物理 call_id 去重。

## 复用与最小修改

HTTP 层使用 FastAPI，直接编排 StoryWorkflowService 的 prepare/revise/approve/generate；没有新增 Mode、Recipe、模板、前端或文学评分体系。服务化大纲任务使用 service_plan_tasks；正文仍使用既有 story_jobs，审批与正式任务入队同一事务完成。API 不承担模型长调用。

角色接入复用 character_templates、character_profile_revisions，新增资产引用与版本化校验。服务器冻结 Canon、Runtime、角色及 performer 映射；Canon 固定事实不能覆盖，Runtime 使用 expected_revision。StoryDelivery 1.0 保持稳定，适配器按句输出 speech/cue，完整保留演绎字段及资产引用。

任务使用 SKIP LOCKED、数据库 wall clock、心跳与执行令牌阻止并发重复发布。进程中断标记过期失败并要求显式重试；取消阻止后续模型请求及发布。失败候选稿不生成正式版本，历史版本不被改写。迁移004兼容已有003及全新库。

实测推动两处创作链最小修复：正文提示明确把未兑现约定写入连续性增量；时间解析器正确识别无前缀“零点”，同时保留数量/电压过滤和证据覆盖校验。没有增加模型调用步骤。

## 工程验证

- 修复提交全量回归 **685 通过**：671 单元/回归、14 PostgreSQL 集成。[JUnit](../../6-实测产物/phase11_engineering/final_9517b0d.xml)
- 最终 Docker 镜像中 **34 通过**：HTTP/角色合同、PostgreSQL、时间校验；另已验证构建、全新迁移、API 启动与健康检查。[容器 JUnit](../../6-实测产物/phase11_engineering/container_final_9517b0d.xml)
- 验证未经审批拒绝生成、修订与过期版本冲突、跨身份隔离、幂等重放、并发领取、取消、失败回滚、重复执行、过期租约 fencing、真实杀死 Worker 后显式恢复及心跳续期。
- 真实 HTTP 验证 API 重启保留排队大纲，重启 API/Worker 后完整故事一致；修复后正式生成任务排队期间再次重启 API，任务保留。
- pg_dump/pg_restore 在独立数据库恢复全部角色、Plan、审批/任务、版本数据；逐行完整哈希与计数一致。[恢复证明](../../6-实测产物/phase11_live_20261015_v3/database_restore.json)
- 最终库保留 2 个角色、4 个 Plan、5 个 Plan Revision、4 个正式生成 Job、4 个故事版本、5 个大纲任务。个性化 Runtime 版本与冲突保护由工程合同测试验证；真实主链使用角色默认 Runtime。
- 所有发布台词都能在真实 writer 原始响应找到；语义引用匹配正文行号；Revise 保留内容哈希一致；Continue 第二新增集的实际请求包含第一新增集事实；演绎任务逐项匹配正文、performer、标注。

初次工程运行中的迁移重复列、测试断言字段名和旧版手工 Golden 脚本被 pytest 误收集问题均已修复，原失败 XML 保留。Golden 脚本仍可按原 CLI 方式运行，pytest 收集限定 test_*.py。

## 失败、修复与样本限制

1. v1 Create：HTTP/正文/导出成功，但连续性状态遗漏未兑现的三声铃。后置验收失败，后续阻塞；旧记录器的 passed 只代表前面的传输检查。[追加裁定](../../6-实测产物/phase11_live_20261015_v1/acceptance_adjudication.json)说明该样本最终失败，原记录未覆盖。
2. v2 Create 首次正式生成：时刻 02:05 缺少 Judge 覆盖证据，READY 门阻止发布；同一获批任务显式重试后成功。
3. v2 Remix 两次正式生成：Judge 的保留、变更、日历要求全部通过，但解析器忽略正文“零点”并误报未落地时刻。两次失败稿均保留，均未发布；修复解析器后第3次真实生成成功。

共 8 次正式生成执行，5 次发布成功、3 次失败（37.5%，含两次已修复的解析器误判）；其中首轮 Create 发布成功但专项后置验收失败。按操作验收尝试计 6 次，4 次通过、2 次失败；最终沿同一来源故事完成四操作。提供商请求本批次无传输失败。以上为单故事设定的增量修复验收，样本不足以推断大规模稳定性；不得把最终4项通过解释为首尝试100%成功。

## 模型用量与耗时

包含所有失败、重试、修复、Plan Revision；复制证据和继承 Plan trace 不重复累计。单位：Token、秒。Reasoning 已包含在 output；缓存读取单列，不再加到 Token 总量。

|操作|物理调用|输入|输出|其中 reasoning|缓存读|模型请求秒|操作壁钟秒合计|
|---|---:|---:|---:|---:|---:|---:|---:|
|Create|31|178,634|159,382|120,919|14,976|1368.273|1392.953|
|Revise|4|97,414|14,721|10,773|0|114.553|124.907|
|Continue|10|157,696|50,956|38,160|0|419.309|424.297|
|Remix|30|525,181|133,183|102,374|87,040|1027.792|1050.500|

**总计：75 次调用，输入 958,925，输出 358,242，输入+输出 1,317,167 Token。** 其中 reasoning 272,226；缓存读 102,016、缓存写 0。估算输入 0，未知用量调用 0，未完成捕获 0。模型请求累计 2929.927 秒，三次运行壁钟累计 3056.125 秒；修复开发时间不包含在模型批次壁钟内。

|阶段|调用|输入|输出|模型请求秒|任务执行壁钟秒（含失败尝试）|
|---|---:|---:|---:|---:|---:|
|generation|68|812,565|327,122|2604.636|2620.207|
|plan|5|132,405|23,891|258.151|260.855|
|plan_revision|2|13,955|7,229|67.140|68.000|

|节点|调用|输入|输出|模型请求秒|
|---|---:|---:|---:|---:|
|episode_writer|15|424,423|86,246|780.909|
|outline|7|146,360|31,120|325.291|
|performance_annotation|45|100,738|214,056|1606.705|
|storycraft_operation_judge|8|287,404|26,820|217.022|

实际返回模型为 qwen3.7-max、qwen3.6-flash；Provider 为既有 openai_compat 网关。大纲提交/审批返回202，示例 Create 提交0.109秒、审批0.359秒；长模型任务在后台完成。没有服务时延SLA或规模压测结论。网关没有返回计费金额/费率，本报告不估算人民币费用。

[完整分操作/阶段/模型统计与物理调用清单](../../6-实测产物/phase11_engineering/model_statistics.json)。可重复运行 `tests/acceptance/phase11_statistics.py`；必须同时传入 v1/v2/v3 才包含全部失败费用。`phase11_evidence_review.py` 可重做逐句来源、引用、哈希及演绎字段校验，`phase11_database_restore.py` 可重做隔离恢复。

## 交付与后续联调

- [服务部署与错误码/恢复约定](DEPLOYMENT.md)、[OpenAPI快照](openapi.json)，运行服务提供 /docs。
- [四操作完整请求及客户端](../../examples/phase11/README.md)，角色 Canon/Runtime/资产样例和导入格式齐备。
- [TTS与客户端联调](TTS_CLIENT_INTEGRATION.md)：逐句任务稳定ID、声音映射、拼接顺序、降级记录与真实音频/设备回执边界。
- 当前范围内无未解决交付阻断缺陷。真实 TTS 平台、最终角色素材、App/管理页面和硬件联调按约定留待后续；本轮模拟 Consumer 不代表音频或设备成功。
- 分支 codex/phase11-product-service，PR目标 codex/story-operations-audit；不合并 main。所有模型证据只保留脱敏请求正文/响应和用量，凭据通过环境注入。
