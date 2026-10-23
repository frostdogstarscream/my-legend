# 我的传说：MOMA 故事模型配置

2026-10-23 运维入口补充：本机 H5 场景与素材导入、校验、离线导出及版本关系见 [视觉演绎与场景文档导航](../../docs/requirements/my-legend/视觉演绎与场景文档导航_20261023.md)。本文件以下为早期模型配置记录，当前创作流程以仓库首页所链的最新说明为准。

2026-10-19声画接入：真实配音、实测时间轴、基础分镜成片和App播放已完成，102.32秒样例保持待审。启动命令不变，操作与边界见[声画制作接入与验收](声画制作接入与验收_20261019.md)。下文保留故事模型配置和早期探测记录。

本次配置仅作用于本项目的故事模型。Base URL 为 `https://moma.cmecloud.cn/v1`，实际请求路径为 `/chat/completions`，模型为 `qwen/deepseek-v4-pro`，非流式调用。不要把完整 `/chat/completions` 地址填入 Base URL。

密钥通过安全输入保存为 Windows 当前用户 DPAPI 密文，位于被 Git 忽略的 `runtime/my-legend-production/story-provider/`。源码、前端 `.env`、测试报告和日志不保存明文密钥。其他电脑或其他 Windows 用户需要重新配置；更换密钥时再次运行配置脚本，历史配置和密文保留。

在项目根目录执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\ops\my-legend\Configure-StoryModel.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\ops\my-legend\Test-StoryModel.ps1
```

第一条命令隐藏输入密钥。第二条会进行一次真实模型调用，仅请求 `{"ok":true}`，最多 256 输出 token，无自动重试，可能消耗供应商额度。保留 TLS 证书验证，拒绝跳转；报告只保存状态、模型、结束原因和用量，不保存请求头或原始响应。报告成功只证明该账号能调用目标模型并返回这个小 JSON，不证明完整故事质量、长输出能力或下游制作可用。

示例里的 `131072` 保存为供应商输出上限记录，不会把每个工作流节点都改成这个额度。正式节点继续使用 StoryGenerate 各节点的 token 策略。本次小 JSON 检查还带 `thinking: {type: disabled}`，这是请求参数；不能仅凭成功就宣称供应商实际关闭了推理。

原 StoryGenerate 已支持两个模型地址/凭据环境变量族以及五个模型档位。准备好该项目源码、其 Python 依赖、数据库和服务认证后，使用本启动器为子进程注入配置：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\ops\my-legend\Start-StoryWorker.ps1 -EngineRoot 'D:\实际的StoryGenerate目录' -Python 'D:\对应虚拟环境\Scripts\python.exe'
```

启动器要求外层环境已安全配置 `STORY_DATABASE_URL` 和 `STORY_API_KEYS`，并覆盖子进程的 FAST、BALANCED、STRONG、LONG、STRONG_ALT 档位，防止只改一个模型后其他阶段继续使用旧模型。不会修改其他项目或系统全局环境。

配置作用于此启动器创建的新 Worker。已经运行的 Worker 不会自动重新加载；已有审批快照中的模型映射也不应被静默改写，新制作任务需要重新创建大纲并确认。首次配置和后续启动应使用同一正常 Windows 用户上下文，隔离环境保存的 DPAPI 密文可能无法在普通终端解密。

2026-10-19 后续接入：已增加独立 PostgreSQL、原 StoryGenerate API/Worker、本机业务接口，以及原型的大纲确认和章节结果页面。现在使用 `Start-Prototype.ps1` 启动完整本机环境，详见 [真实故事接入说明](故事服务接入说明_20261019.md)。本工具不绕过大纲审批，也不提供替代故事引擎。前端仍不携带模型密钥。

完整真实样例已通过：1章、3场、39条文本事件，文本估算90秒，READY/PASSED，浏览器已打开阅读；声音与视觉尚未制作。实际失败历史、29次模型调用用量、测试范围与视觉容量限制见[本轮验收记录](联调验收记录_20261019.md)。

2026-10-19 初始连通性探测：一次真实 JSON 请求返回 HTTP 200，供应商报告模型 `deepseek-v4-pro`，结束原因 `stop`，严格检查仅包含 `ok: true`，用量为输入 33、输出 5、合计 38 token。该探测单独只证明微型JSON请求，后续完整接入的结果以[真实故事接入说明](故事服务接入说明_20261019.md)及其验收记录为准。脱敏探测证据见 `evidence/moma-json-probe-20261019.json`。
