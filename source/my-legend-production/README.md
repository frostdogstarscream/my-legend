# 我的传说：故事到声音和视觉的接入工程

2026-10-19真实制作扩展：已为正式故事生成23条真实TTS、11条真实音效，保留5条原文动作卡，交付102.32秒基础分镜并接入uni-app H5；声音和视觉效果待人工审核。操作、制品、34次制作调用与1次探测、缓存与审核回写范围见[本轮接入验收](../../ops/my-legend/声画制作接入与验收_20261019.md)。以下0.1.0内容保留为此前离线基线。

版本 0.1.0，2026-10-15。实现两份 v1.0 适配契约，并在 Windows 上接入原声音 LangGraph、原视觉 LangGraph 和 Dart 规则编排器。

首轮交付包括通用适配器、制品登记与哈希校验、幂等准备记录、两个工作流的进程宿主、新联合对齐、离线预览和基础像素视频导出。用户本轮选择先完成代码和本地联调，因此整个验证过程没有调用真实模型。

2026-10-19 补充：按用户要求为故事模型准备了 MOMA `qwen/deepseek-v4-pro` 的加密本机配置、受限 JSON 检查和 StoryGenerate Worker 启动器，见 [配置说明](../../ops/my-legend/README.md)。此次单独进行了一次真实小 JSON 连通性检查，不属于下述 2026-10-15 离线联调，未运行整篇故事或改变 App 的 Mock 状态。

## 当前可查看的结果

本机运行目录为 `D:/Code/AIToy/runtime/my-legend-production`。最新完整样例位于 `runs/local-004`，其中：

| 路径 | 内容 |
| --- | --- |
| integration-report.json | 来源版本、制作和审核状态、联合映射、混音派生与视频信息 |
| prepared/ | 两个适配结果、原目标输入和来源映射 |
| audio-input/ | 明确标记的合成测试信号及其哈希、运行输入 |
| audio-workflow/ | 原声音图的检查点、分项素材、混音、时间轴和审阅包 |
| visual-workflow/ | 原视觉图的检查点、真实 Dart 编排结果及待确认信息 |
| preview/index.html | 以音频时钟驱动画面、角色和字幕的本地预览 |
| preview/scene.mp4 | 1280×720、10fps、4.7 秒的基础像素测试视频，含合成音轨 |

样例中的 READY 和批准计划 ID 是协议测试数据，不表示 StoryGenerate 本轮已生成或批准新故事。合成信号用于验证媒体处理、时长和播放器，不能用于评价 TTS 音质、情绪或模型效果。声音停在 awaiting_review，视觉停在 awaiting_context，发布为 not_approved。

## 本机快速运行

独立虚拟环境已经配置好，可在 PowerShell 使用：

```powershell
$legendPython = 'D:\Code\AIToy\runtime\my-legend-production\.venv\Scripts\python.exe'
& $legendPython -m my_legend_production.cli run-local --root 'D:\Code\AIToy\runtime\my-legend-production\runs\my-local-test' --paths 'D:\Code\AIToy\runtime\my-legend-production\workflows.json' --video
& $legendPython -m my_legend_production.cli serve --root 'D:\Code\AIToy\runtime\my-legend-production\runs\my-local-test\preview' --port 8896
```

服务只监听 127.0.0.1。打开打印的地址，可播放、暂停、重播和定位；服务支持 WAV/MP4 的 HTTP 字节范围请求。浏览器中的“查看测试视频”打开视频文件，字幕由网页独立显示，未烧录进视频。

同一 root 重放会复用已准备的制品及原工作流检查点。输入或配置改变时使用新 root，不能通过覆盖旧输入继续一项制作。适配器升级改变映射规则时，应更新上下文 profile_revision 并重新准备，保留旧幂等记录。状态查询：

```powershell
& $legendPython -m my_legend_production.cli status --root 'D:\Code\AIToy\runtime\my-legend-production\runs\my-local-test'
```

## 适配真正的故事和本地素材

`prepare` 是通用接口，既不依赖五行样例，也不执行模型调用。生产默认只解析当前 store 登记过的 artifact 引用；fixture 引用只有显式提供 fixture-root 的文档联调模式才可读取。

1. 用 register 将冻结的 StoryDelivery、RenderManifest、能力上下文及资源登记到本次 store。命令返回 `{ref,sha256}`。声音/视觉上下文里的 capability_profile 也应使用已登记引用。
2. 按 contracts/adapter.schema.json 组装 audio、visual 两份 Request，使用同一来源版本和场景范围。每种 Request 的 context 指向各自上下文。
3. 调用 prepare。得到 prepared 后可导出 target/sidecar；needs_context 和 blocked 不提交工作流。
4. 为音频目标的每个 block_id 登记已有 WAV，建立 media-bindings.json：键为目标 block_id，值为登记返回的 `{ref,sha256}`。本地联调要求覆盖全部音频事件，不能用错音轨补缺项。
5. 用 run-media 运行两个原工作流和新联合模式。本命令消费本地素材，不发起 TTS/SFX；素材原先是否为合成信号，由来源及人工核对确认，报告不自动宣称为真实 AI 产物。

```powershell
& $legendPython -m my_legend_production.cli register --store 'D:\my-job\store' --file 'D:\my-job\StoryDelivery.json'
& $legendPython -m my_legend_production.cli prepare --store 'D:\my-job\store' --request 'D:\my-job\audio.request.json' --export-dir 'D:\my-job\audio-prepared'
& $legendPython -m my_legend_production.cli prepare --store 'D:\my-job\store' --request 'D:\my-job\visual.request.json' --export-dir 'D:\my-job\visual-prepared'
& $legendPython -m my_legend_production.cli run-media --store 'D:\my-job\store' --audio-request 'D:\my-job\audio.request.json' --visual-request 'D:\my-job\visual.request.json' --media 'D:\my-job\media-bindings.json' --root 'D:\my-job\run-001' --paths 'D:\Code\AIToy\runtime\my-legend-production\workflows.json' --video
```

生产方的真实批准、资源绑定和能力记录需要可信业务来源。仅将一个 JSON 字段写成 READY/verified 不构成真实验收。按业务调用方隔离 ArtifactStore，HTTP 服务封装时从鉴权主体取得 caller；这里的 CLI 假定操作者已获本机文件访问授权。

## 结构与实现

| 模块 | 职责 |
| --- | --- |
| core.py | 严格 JSON、Schema、来源和 Manifest 逐行核对、ID 和权重规则 |
| storage.py | 受控制品解析、原字节哈希、不可变对象、SQLite 幂等记录 |
| adapters.py | 声音和视觉转换、上下文/能力/在场检查、完整 coverage 与准备结果 |
| worker.py | 原声音图和视觉图的独立进程宿主，保留原审核中断 |
| alignment.py | my-legend.joint/0.3：旁白身份、独立音轨、视觉动作和实测帧投影 |
| local_demo.py | 通用本地素材联调与明确标记的合成样例入口 |
| render.py、server.py | 基础像素视频导出、纯画面及音轨检查、可定位的本机媒体服务 |

目标沿用 lingban.script/1、visual_request/1.0、visual_script/1.0。旧 joint_v02 继续阻断不满足旧规则的输入；新联合模式不修改两侧原协议。具体实现细节和来源预检错误见 [协议补充](docs/协议补充_v1.0.1.md)。

## 在另一台机器建立环境

需要 Python 3.12、原用户提供的两个固定 ZIP、Dart 3.11 或更高的兼容版本；视频导出另需带 H.264/AAC 的 FFmpeg。Python 固定依赖见 requirements.lock.txt；当前验证使用 Dart 3.13.3。Dart 来自[官方存档](https://dart.dev/get-dart/archive)，不要求安装整套 Flutter 来执行这部分纯 Dart 规则。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
python tools/bootstrap_dependencies.py --audio-zip '声音开发包.zip' --visual-zip '视觉开发包.zip' --runtime 'D:\legend-runtime'
```

在独立的 dart-core 目录建立以下 pubspec.yaml，再用指定 Dart 执行 pub get。可将 PUB_CACHE 指向该 runtime 下的独立目录。

```yaml
name: my_legend_rules_host
environment:
  sdk: ">=3.11.0 <4.0.0"
dependencies:
  characters: 1.4.1
  crypto: 3.0.7
```

```powershell
python tools/configure_runtime.py --runtime 'D:\legend-runtime' --dart 'D:\dart-sdk\bin\dart.exe' --packages 'D:\legend-runtime\dart-core\.dart_tool\package_config.json' --ffmpeg 'D:\ffmpeg\bin\ffmpeg.exe'
```

bootstrap 校验原 ZIP 的 SHA256 和 CRC，只向新目录解压。configure_runtime 只对隔离副本增加 Dart 可执行文件与 package-config 的注入，保留原编排规则，并保存代码哈希。原 ZIP 和已有项目不被修改。

## 验证与后续接入

新增适配与本机媒体服务测试、原声音包回归、视觉输入/工作流回归及端到端结果分别存入 evidence。测试使用固定环境和假/本地素材，不将技术测试结果替代人耳、视觉或真机验收。

本机执行新增测试可使用：

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp 'D:\legend-runtime\new-test-directory'
```

用新测试目录保留历史证据；其中媒体服务测试需要允许本机回环连接。运行端到端本地联调还需上面的原工作流、Dart 和媒体依赖。

本版先开放 preserve、基础人物和明确动作。精确模型控制、enrich 锁定检查、新人物图集和长剧分段仍按能力报告处理。后续真实模型接入需要配置服务端 provider profile、模型与音色、调用预算及环境凭据，再从短场景开始验证；业务 API、uni-app 联动和 Android/小程序真机验收另行推进。
