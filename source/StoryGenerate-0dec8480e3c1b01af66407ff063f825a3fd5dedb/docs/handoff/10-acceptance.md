# 10 本轮验收结论与接手签收

本轮是交付资料专项，基线eeb99ae，不修改引擎行为。已验收Phase11分支保持原样；本分支包含手册、完整样例展示、可执行客户端、离线构建/复核和无模型HTTP探测。新增模型调用为0，没有将测试替身说成真实生成。

|验收项|依据与本轮结果|
|---|---|
|接口覆盖|26业务操作/24路径、50Schema与匿名文档路由完整列出；实际路由/OpenAPI与基线逐项一致|
|示例请求|四操作归档请求、森林导入/Create、Plan反馈/审批、Runtime patch经现有Pydantic校验；占位值明确需替换|
|演示工具|Python/PowerShell/curl均提供逐步调用；scope请求工具从真实Delivery取得ID，不自动审批或重试|
|真实样例|7份完整原文Markdown与原始JSON索引；逐集逐行哈希/全文来源复核|
|质量与成本|全文12维度及逐篇引用；Phase11原始capture与75调用匹配，复算1,317,167 Token；Phase10历史失败与未知用量保留|
|真实HTTP无模型复核|[40项探测](contracts/http-probe.json)，真实uvicorn socket+恢复的PG16隔离库，无Worker、无模型；含Python/PowerShell/curl健康调用，导出/演绎/角色/幂等/取消等|
|数据库恢复|[源/恢复表行hash一致](contracts/database-restore.json)，不修改原验收库|
|工程回归与链接|[本轮最终验证记录](contracts/verification.json)、[回归摘要](contracts/regression-summary.json)、[原始JUnit](contracts/regression.xml)、[敏感信息检查](contracts/security-check.json)；685项通过，数值以记录为准|
|Git交付|本页所在远程分支与PR承载正式提交；未合并main，未替换旧验收分支|

探测的完整响应优先复用原档案：接口成功示例索引的`probe_response_reference`定位`http-responses.json`中的entry及response；其中的`response_reference.archive_path`再从仓库根解析，`json_pointer:/response`提取原包装体；canonical_sha256用UTF-8、ensure_ascii=False、sort_keys=True的JSON计算。列表可能逐项引用。其余为完整实际响应。这样保留本轮比对证据，又不重复提交大块故事。请求中的随机ID为新隔离演示资源，不是旧真实模型任务；探测未声称新Plan/Revision/正文模型成功。

可重复的离线验证（不需要模型凭据）：

```sh
python tools/handoff/build_reference.py
python tools/handoff/build_samples.py
python tools/handoff/recompute_cost.py
python tools/handoff/build_endpoint_examples.py
python tools/handoff/verify.py
# 已配置测试数据库时运行现有工程回归
python -m pytest -q
```

`probe_http.py`需要项目Docker PostgreSQL中的原Phase11验收schema和安全注入STORY_DATABASE_URL，会恢复到新数据库再执行探测，不创建模型Worker；没有旧验收数据库的接手者可直接查看归档证据，不假称本机已重放。通用新环境启动见[09](09-deployment.md)，付费生成手动流程见[03](03-manual-demos.md)。

本轮首轮回归遇到Windows默认pytest临时目录拒绝访问，属于执行环境问题；切换到新的可写`--basetemp`后重跑，最终结果与首次失败原因一并记录，未改测试以跳过。既有Phase11的685工程/34容器测试是**历史**结果，不能与本轮回归相加冒充新增数量。

## 接手真人签收（尚未由独立同事执行）

本助手完成代码、脚本与证据复核，不是“不懂内部实现的新同事”。下列接手实验应由真实负责同事在自己的受控环境勾选，暂不宣称完成：

- 按README/09从空环境启动API+Worker，确认角色导入/选择。
- 按演示1/2生成大纲、反馈、重新审批并取得正文，记录实际ID/失败/成本。
- 按演示3/4/5完成Revise、两次Continue、Remix，查看旧版本与谱系。
- 导出JSON/台本/角色/RenderManifest，模拟consumer验证，不把它当音频。
- 按04说明自己的播放状态存储与安全切换职责，明确精确切片尚缺实现。
- 阅读真实全文/质量/成本，确认文学修订、年龄适配及真实TTS试听责任。

没有实际真人签收、真实TTS、设备播放或新题材真实模型结果，这是明确的交付限制。本包可支持联调启动，但不能给这些未执行项盖“通过”章。

## 已知限制及风险

当前年龄档最低14-17，无低龄分级/儿童成品验收；单广播站母题、最多五集，题材/长篇/真实高并发泛化待测。部分故事需轻度文字审阅，S04保险丝行动链需局部修复；归档样例未润色。实际音频时长、供应商音色映射与cue效果待声音团队试验。无公开播放进度写入、精确场景内已听前缀保护、音频切片拼接、切换通知、音频状态回写。Plan已审批后不能原地修改；CANCELLED不可retry；模型在途取消仍可能收费。无实际供应商费率，未给人民币成本。所有这些边界与已有工程/真实模型通过事实同时成立。
