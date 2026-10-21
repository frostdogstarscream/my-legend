# 固定工作流源码快照

本目录保存本机当前实际使用的音频和视觉工作流源码、必要规则/静态资源，以及音频开发测试文件。2026-10-21 从现有隔离依赖目录导出，未改写工作流内容；不包含密钥、数据库、生成媒体和供应商响应。

`dependency-lock.json` 是导出时的内容哈希清单，路径相对于 `runtime/my-legend-production`。来源版本及原始归档哈希见 `../my-legend-production/source-baseline.json`。Git 属性关闭此快照的换行转换，以保持哈希。

在新克隆中运行 `python ops/my-legend/restore_workflow_sources.py`，可恢复到一个尚不存在的 `runtime/my-legend-production/dependencies` 目录。已有运行目录不会覆盖。之后按照生产后端 README 安装 Python/Dart 依赖，并运行 `configure_runtime.py` 配置本机路径。

这里包含视觉源码供版本保存，当前应用的真实验收范围仍为剧本和音频，视觉制作暂停。恢复工具不会启动服务或调用模型。
