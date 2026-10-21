# AIToy 项目目录整理规则

更新时间：2026-08-25

## 1. 整理目标

本工作区此前同时放置了多套 TTS 源码、模型检查点、Ascend wheel、部署脚本、远程诊断脚本、验证输出、日志与项目文档，根目录难以判断文件用途。本次整理不删除任何模型、检查点、日志、验证证据或源码；仅按用途归类，并为原顶层目录保留兼容链接。

整理时共迁移：20 个顶层目录和 159 个顶层文件。工作区本身不是 Git 仓库，因此这些移动需要以本文件的映射和兼容链接作为可追踪基线。

## 2. 顶层目录约定

```text
AIToy/
├── source/       # 可编辑源码、PoC、Web/语音应用及其项目内测试
├── test/         # 跨项目验收、集成、验证和实验性测试脚本
├── docs/         # 运行手册、报告、设计记录和目录规则
├── ops/          # 部署、远程传输、诊断、实验和容器相关脚本/配置
├── artifacts/    # 模型、检查点、wheel、第三方归档等不可随意改动的资产
├── evidence/     # 验收 JSONL、部署日志、历史输出和可追溯证据
├── runtime/      # 本机缓存和 Python 运行时临时内容
└── <兼容目录链接> # 旧顶层路径，临时保留以避免既有脚本失效
```

## 3. 新文件放置规则

| 类型 | 放置位置 | 规则 |
|---|---|---|
| 可维护业务源码 | `source/<project>/` | 每个项目应保留自己的 `README`、依赖文件、测试和配置。不要在根目录新建业务 `.py`。 |
| 新的底座模拟器 | `source/device-simulator/` | 模拟器代码、协议和配置应独立于现有 TTS PoC。跨项目端到端测试放入 `test/integration/`。 |
| 项目内单元测试 | `source/<project>/tests/` | 与源码一起维护、一起运行。 |
| 跨项目验收测试 | `test/acceptance/` | 例如真实网关、TTS 服务、固定文本或音频验收。 |
| 集成/端到端测试 | `test/integration/` | 例如 Dify → TTS → 两个模拟底座的完整流程。 |
| 试验性测试 | `test/experiments/` | 结果未冻结或只用于探索的测试。 |
| 用户/开发文档 | `docs/` | `runbooks/` 放运行手册，`reports/` 放结论/报告，`records/` 放短文本记录。 |
| 部署、启动和服务脚本 | `ops/deploy/` | 不得把未验证的脚本称为生产部署入口。 |
| 上传、下载、SSH、远程辅助脚本 | `ops/remote/` | 禁止在脚本、日志和文档中写入可复用凭据。 |
| 诊断脚本 | `ops/diagnostics/` | 只读探针、状态检查、日志采集等。 |
| 临时修复或试验脚本 | `ops/experiments/` | 以 `_` 开头的历史脚本集中保存，不能直接当成正式运行入口。 |
| Ascend/服务配置 | `ops/config/ascend/` | 修改前先确认目标服务器和服务版本。 |
| 模型、检查点、wheel、压缩包 | `artifacts/` | 视为受控资产；新资产应带来源、版本、哈希或清单。 |
| 日志和验收输出 | `evidence/` | 只追加和归档，不作为源码或文档编辑。 |
| 缓存、临时输出 | `runtime/` | 可再生内容；不要手工混入源码或验证证据。 |

## 4. 当前主要源码位置

| 项目 | 规范位置 |
|---|---|
| Qwen3-TTS NPU7 PoC | `source/qwen3-tts-npu7/` |
| Qwen3-TTS 4090 相关资产 | `source/qwen3-tts-4090/` |
| GLM-TTS NPU7 v2 | `source/glmtts-npu7-v2/` |
| GLM-TTS 原始/准备源码 | `source/GLM-TTS/`、`source/GLM-TTS-src/` |
| GLM-TTS NPU7 PoC | `source/glm-tts-npu7-poc/` |
| Step-Audio/参考运行时 | `source/npu_reference/` |
| 语音通话 Web 应用 | `source/voice_call/` |
| 双龙语音剧情相关代码 | `source/double_dragons/` |

`source/assets/` 是当前语音应用所需的共享音频资产。新项目的资源应优先放在各自项目内，避免继续扩大共享资产目录。

## 5. 资产与证据位置

| 原位置 | 规范位置 |
|---|---|
| `models/` | `artifacts/models/` |
| `GLM-TTS-ckpt/` | `artifacts/checkpoints/GLM-TTS-ckpt/` |
| `ascend_wheels*` | `artifacts/wheels/` |
| 根目录 `*.tar.gz` | `artifacts/archives/` |
| `glmtts_fullcheck_20260805_1425/` | `evidence/glmtts_fullcheck_20260805_1425/` |
| 根目录部署日志 | `evidence/stepaudio2-deployment/` |
| 根目录 GLM-TTS JSONL 记录 | `evidence/glmtts-validation/` |
| `.cache/` 和 `__pycache__/` | `runtime/cache/` 和 `runtime/python-cache/` |

## 6. 原路径兼容策略

为避免已有脚本、历史运行手册和可能仍在使用的外部命令因路径变化而立即失效，以下原顶层目录已保留为 Windows Junction（目录链接）：

```text
qwen3-tts-npu7/      -> source/qwen3-tts-npu7/
voice_call/           -> source/voice_call/
GLM-TTS-src/          -> source/GLM-TTS-src/
models/               -> artifacts/models/
ascend_wheels*/       -> artifacts/wheels/ascend_wheels*/
ascend_configs/       -> ops/config/ascend/
.cache/               -> runtime/cache/
```

其他已迁移项目目录、检查点和验证证据也采用同样的兼容策略。根目录兼容链接不是新的真实存储位置；新增文件必须写入上述“规范位置”。

兼容链接在以下条件全部满足前不要移除：

1. 已确认没有本机进程或远程上传脚本依赖旧路径。
2. 所有仍需保留的运行手册已更新到规范路径。
3. 对应项目的启动、测试和上传流程已在新路径通过验证。

## 7. 路径与脚本规则

1. 新 Python 代码应使用 `Path(__file__).resolve()` 推导项目内路径，不要把 `D:\Code\AIToy\...` 写死在代码中。
2. 配置中的模型路径、输出目录和缓存目录应通过环境变量或显式配置传入。
3. 新增部署脚本必须说明：目标环境、前置条件、启动/停止行为、健康检查、日志位置和回滚方式。
4. 诊断脚本默认只读；涉及远程服务、容器、模型或凭据的变更必须明确授权后执行。
5. 每次产生新的远程部署包、模型包或验收记录时，应放在 `artifacts/` 或 `evidence/`，并保留版本、时间和哈希信息。
6. 不在文档、日志、测试样本或提交中保存 API Key、SSH 密码、设备密钥、Token 或完整认证头。

## 8. 后续建议

本次整理先建立了稳定的文件职责边界，未重写历史脚本的内部逻辑。后续新建“两个电脑底座模拟器”和 Spring Boot/若依后台时，应分别落在 `source/device-simulator/` 与独立的 `source/ai-toy-platform/` 项目中，并从第一天开始使用本规则。
