# 灵伴通用 LangGraph 开发包 v0.2.1

先阅读 [完整使用文档](docs/guides/LangGraph使用文档_v0.2.1_20261013.md)，再看 [版本说明](docs/guides/v0.2.1版本说明.md)。安装与执行目录是 pipeline/。旧说明和报告保留作历史，以本完整文档、当前源码与配置为准。

## 首次运行

1. 解压并进入 pipeline，准备 Python 3.12 独立虚拟环境。
2. 按完整使用文档安装 requirements.lock.txt 和本地项目。
3. 执行 tools/run_offline_demo.py --root ./offline-output；该目录必须不存在。
4. 查看生成的 offline-output/production/drafts/r1/preview.html。预期为 awaiting_review、技术通过、外部调用0。
5. 真实生成先复制相应模型配置，填写角色音色和调用预算，使用 tools/run_secure.py 隐藏输入Key。

## 交付内容

- pipeline/：v0.2.1源码、公共Schema、配置模板、测试、工具、依赖锁定文件。
- docs/：完整使用说明、版本说明、方案、交接契约及历史验证证据。
- contracts/handoff.md、AGENTS.md：协作与版本交接约定。
- source_manifest.json：包内文件SHA256。

不包含凭据、虚拟环境、模型权重、运行SQLite或作品媒体。此前试听包独立保留。人工听审、技术检查、引擎执行和发布批准分别记录。新制作使用新根目录；不直接升级旧未完成检查点。

本次交付校验记录位于 docs/reports/evidence/v0.2.1-handoff/。其中历史真实联调不代表本次再次调用模型，本次验证只使用本地与合成素材。
