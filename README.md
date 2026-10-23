# 我的传说 / My Legend

基于 uni-app 的故事与广播剧创作原型。独立 API 和 Worker 串联 StoryGenerate、声音编排、真实 TTS、音效缓存、实测时间轴和混音，生成结果返回 App。

## 当前状态（2026-10-21）

- 已接入新建、指定版本修改、续写和改编；保留源版本及新版本关系。
- 当前创作入口使用先试听策略，自动制作后交付，不等待人工内容审批；技术完整性检查保留。
- 真实模型和 H5 播放已验证；听感仍需用户判断。本仓库不包含本机运行数据库或历史音频。
- 视觉代码保留，视觉制作暂停；尚不代表 APK、小程序或生产环境部署验收完成。

## 源码布局

| 路径 | 内容 |
|---|---|
| `source/fingertip-legend-app` | uni-app / Vue 3 / TypeScript 前端 |
| `source/my-legend-production` | 业务 API、生产 Worker、声音桥接与混音适配 |
| `source/my-legend-stage` | Spine 2.1 战斗／生活动作舞台、定时换装与现有配音联动 |
| `source/StoryGenerate-0dec8480e3c1b01af66407ff063f825a3fd5dedb` | 本机使用的 StoryGenerate 源码及适配修订 |
| `source/my-legend-workflow-dependencies` | 固定音频/视觉工作流源码快照与内容哈希 |
| `ops/my-legend` | Windows 本机启动、配置及验证工具 |
| `docs/requirements/my-legend` | 需求、契约与阶段验收记录 |

## 前端快速体验

需要 Node.js 和 npm。在 `source/fingertip-legend-app` 下运行：

```powershell
npm ci
Copy-Item .env.example .env.local
npm run dev:h5
```

默认是 Mock 演示。真实后端配置见 [本机接入说明](ops/my-legend/故事服务接入说明_20261019.md)。原真实 App 使用 5193；本机的 5194 独立演示副本未纳入本仓库。

## 真实后端与模型配置

新机器需要重新建立 Python 3.12 环境、PostgreSQL、Dart/FFmpeg 工具和本机路径配置；克隆代码不会复制当前电脑的服务、数据库和凭据。

1. 通过 `python ops/my-legend/restore_workflow_sources.py` 恢复固定依赖源码到新的运行目录（不会覆盖已有依赖）。
2. 按 [生产后端说明](source/my-legend-production/README.md) 安装依赖和配置 `workflows.json`；故事环境的固定 Python 依赖见 `ops/my-legend/requirements-story.lock.txt`。StoryGenerate 与生产包都需要在环境中以 editable 方式安装。启动器使用的本机 PostgreSQL 路径需按机器准备。
3. 使用 `Configure-StoryModel.ps1` 和 `Configure-AudioModel.ps1` 隐藏输入模型密钥。DPAPI 密文仅留在本机 `runtime`，换电脑或 Windows 用户需重新配置。
4. 环境准备后，通过 `ops/my-legend/Start-Prototype.ps1` 启动原真实链路。配置和启动本身不代替模型调用验收，真实生成会产生供应商费用。

最新行为以 [先试听模式](docs/requirements/my-legend/先试听模式_20261020.md) 和 [修改、续写、改编接口](docs/requirements/my-legend/指定版本修改续写改编_20261020.md) 为准；较早文档保留当时的审批和视觉试验记录。

新增的 [Spine 动作预览](source/my-legend-stage/README.md) 可在本机 H5 查看原始动作，并与已交付广播剧联动。[生活演绎 v0.3](source/my-legend-stage/docs/life-stage-v0.3.md) 支持基础关键词编排、三部件定时换装、简表修正及版本保存。素材只在本机导入，不包含口型驱动或对话自动相向／对视，不会触发新的配音生成。

## 仓库范围

本仓库从已有 AIToy 工作区的 Git 初始化提交继续建立，使用明确的目录白名单。其他产品、模型权重、运行目录、凭据、数据库、生成媒体、缓存和 node_modules 均不提交。前端展示必需的静态图像保留。

提交前可运行 `python ops/my-legend/check_git_export.py` 检查候选文件的大小和常见凭据形式；检查不会输出凭据值。该检查不替代人工确认提交范围。

## 原工作区说明

本工作区已经按源码、测试、文档、运维、模型资产、验证证据和运行时数据整理。

目录规则、迁移映射、兼容链接与新增文件放置要求见：

- [项目目录整理规则](docs/PROJECT_ORGANIZATION.md)

新文件不要直接放在工作区根目录。请按上述规则放入对应分类目录或所属源码项目内。
