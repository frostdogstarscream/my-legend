# StoryGenerate 故事生成模块交付包

本交付包供业务后端、App、角色资产及 TTS 团队直接联调。**可进入联调**：四操作、审批、异步任务、PostgreSQL 与结构化演绎交付已有真实验收。完成故事生成不表示音频合成或设备播放成功。

引擎基线：[`eeb99ae66069862878387ce6850fb20ef6ea0586`](https://github.com/zblds-a/StoryGenerate/commit/eeb99ae66069862878387ce6850fb20ef6ea0586)，HTTP API 11.0，StoryDelivery/RenderManifest 1.0，数据库迁移004。本包在独立分支 `codex/storygenerate-handoff` 整理；具体交付提交以本页所在 GitHub 分支的最新提交为准。未修改已验收研发分支，未合并 main。

|入口|内容|先读人群|
|---|---|---|
|[00 交付总览](00-overview.md)|职责、交付清单、复核依据|所有人|
|[01 架构与功能边界](01-architecture.md)|调用链、事务、状态图、四操作范围|后端/维护者|
|[02 完整 API 手册](02-api-reference.md)|26操作、参数、完整请求与响应、50个Schema|业务后端|
|[03 人工演示教程](03-manual-demos.md)|8套逐步演示，PowerShell/curl/Python|联调执行者|
|[04 播放中改变剧情](04-direction-change.md)|四种时机、可行组合、尚未实现的边界|App/产品/后端|
|[05 真实故事展示](05-story-gallery.md)|7份完整台本、JSON、大纲、原始模型证据|内容/产品/TTS|
|[06 质量与成本评估](06-quality-cost.md)|全文审阅、失败样本、逐阶段Token和风险|产品/项目负责人|
|[07 前端与业务后端](07-client-integration.md)|ID、审批、轮询、错误、幂等、角色|App/业务后端|
|[08 TTS 交接](08-tts-integration.md)|逐句字段、资源路由、顺序、模拟消费者|TTS团队|
|[09 部署与排障](09-deployment.md)|Windows/Linux、环境、迁移、备份、版本|运维|
|[10 验收结论](10-acceptance.md)|本轮复核结果、明确限制、接手检查表|负责人|

最短启动：Python3.11 + PostgreSQL16 → 安装 `.[service,test]` → 注入数据库、Bearer和网关环境变量 → `python -m drama_engine.service migrate` → 分别启动 `serve` 与 `worker`。完整可执行命令见[部署](09-deployment.md)，不要跳过凭据配置。

最短调用：导入/选择角色 → `POST /v1/plans` → 轮询 task → GET Plan → 可选反馈修订 → 审阅后 approve → 轮询 job → GET story-version → 导出 RenderManifest。三种客户端脚本及明确人工审批停点见[演示1](03-manual-demos.md)。

已有证据：[Phase10最终专项](../../6-实测产物/operations_report_20261014/acceptance.json)、[Phase11真实HTTP验收](../phase11/ACCEPTANCE.md)、[本轮无模型HTTP验证](contracts/http-probe.json)、[本轮验证记录](contracts/verification.json)。工程测试中的显式替身不计为真实模型验收。本轮复用归档真实模型产物，没有为了制作手册重复生成故事。

其他团队负责用户登录/业务页面、播放进度及版本切换、真实TTS与音频拼接、硬件控制。当前没有播放位置写入HTTP接口、精确毫秒截断拼接或音频状态回写；没有14岁以下儿童分级；没有大规模并发/长篇泛化结论。独立新同事从零走通仍需接手团队实际签收，不能用本轮脚本检查替代真人试用。
