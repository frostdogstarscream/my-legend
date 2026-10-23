# Spine 2.1 本机战斗与生活动作预览

2026-10-23 更新：生活演绎已增加对白朝向、表情轨道、离线表情建议、121 项本地主题素材索引和 15 套固定场景。当前入口、素材准备顺序、版本关系及验证边界见 [视觉演绎与场景文档导航](../../docs/requirements/my-legend/视觉演绎与场景文档导航_20261023.md)。本页下方保留原始舞台接入说明。

生活动作、三部件换装和自动编排已增加为独立 `life_v1` 配置。打开 `http://127.0.0.1:5193/#/pages/performance/index?mode=spine&profile=life_v1`，或在原入口选择“生活动作与编排”。实施、接口和使用说明见 [生活演绎 v0.3](docs/life-stage-v0.3.md)，验证记录见 [2026-10-22 验收](docs/life-acceptance-20261022.md)。以下原战斗入口保持兼容。

独立 TypeScript 舞台适配器，使用 PixiJS 8.16.0 的 WebGL 网格绘制。当前接入用户提供的 Spine 2.1.27 少女前线素材，支持原始动作切换及已交付广播剧联动。没有口型驱动，不把手动选中的战斗动作解释为自动还原剧情。

## 使用

在已配置真实后端的本机打开：

- 动作试演：`http://127.0.0.1:5193/#/pages/performance/index?mode=spine`
- 配音联动：作品页 →「配音动作联动」，或 `#/pages/performance/index?productionId=<已有任务编号>`。

首次进入需点击「进入本机创作」。试演默认选中 HK416MOD 的攻击；其余人物显示待机。切换演员后可选择该演员实际拥有的动作。倒下默认单次，其余动作默认循环，可调整至 0.5 / 1 / 1.5 / 2 倍速。

依赖安装（仓库根目录）：

```powershell
npm ci --prefix source/my-legend-stage
npm ci --prefix source/fingertip-legend-app
python ops/my-legend/provision_spine_runtime.py
python ops/my-legend/prepare_spine_preview.py '本机素材.zip的完整路径'
./ops/my-legend/Start-SpinePreview.ps1
```

`Start-SpinePreview.ps1` 复用本项目已初始化的 PostgreSQL、Python 环境和加密配置，只启动数据库、预览 API、H5；不会启动故事或媒体生成 Worker。已有服务不会被替换；如果 API 仍运行旧代码，应按原服务的生命周期重启，避免直接停止受 supervisor 管理的子进程。

原始压缩包、图集、贴图及官方运行时都保存在本机。运行时固定到官方 `2.1.25` 标签对应提交 `142e770e5b4b42395a3a0a68ff3c8a21ba2774eb`，下载校验 SHA-256 并保留原许可头。素材不进入源码仓库。原始导出文件保持原样；转换 JSON 与目录位于被 Git 忽略的 `runtime/my-legend-spine`。这是旧素材适配路径，不是 Spine 4.2 标准演员模板的实现。

## 接口和时间

- `GET /api/v1/stage-preview/catalog`：本机目录、实际动作及内容哈希。
- `GET /api/v1/stage-preview/assets/{sha256}`：仅返回目录中的文件，同时校验文件长度和哈希；沿用 API 登录验证。
- `POST /api/v1/production-jobs/{job_id}/stage-preview`，正文 `{}` 或 `{"bindings":{"剧情角色ID":"素材演员ID"}}`：读取当前任务已经交付的声音、剧本、实测时间轴。按角色 ID 匹配，最多三个不同演员；默认按对白角色首次出现顺序选取。

预览清单单独写入 `runtime/my-legend-spine/previews`，不修改原任务、音频或生产模块状态。使用同源版本的 AudioMix / MeasuredTimeline 引用，前端再次校验媒体字节哈希。音频模式以 `HTMLAudioElement.currentTime` 为唯一播放时钟；暂停、倍速和拖动后按绝对时间重新采样。素材或 WebGL 失败时保留声音播放。旁白不自动绑定演员，未绑定角色仍显示字幕。

每次采样恢复 setup pose，恢复绘制顺序及网格变形，再应用一条原始动作。抑制素材事件外放，不重复叠加素材枪声。旧 JS 运行时的兼容处理包括翻转轨道索引、稀疏 FFD 补零、插槽独立变形数组、动画时长回填及 setup pose 翻转值回填；不改写原始运行时文件。

## 验证

```powershell
npm run type-check --prefix source/fingertip-legend-app
npm test --prefix source/fingertip-legend-app
npm run build:h5 --prefix source/fingertip-legend-app
node ops/my-legend/verify_spine_math.mjs
runtime/my-legend-story/.venv/Scripts/python.exe -m pytest source/my-legend-production/tests/test_spine_preview.py source/my-legend-production/tests/test_production_playback.py -q
```

2026-10-21 本机验证：三个人物、20 个动作、620 个采样点，全部网格坐标有效，UV/三角形索引合法，反向跳转姿态一致；前端 67 项测试、后端相关 25 项测试通过。真实浏览器显示人物并播放动作，真实任务 `a47ca0f4-9434-4d7e-81ef-b12bf759a1c7` 的 77.84 秒配音及 14 段字幕可联动，预览前后任务数据一致。

当前交付为本机 H5 原型。生活配置已支持基础关键词编排和显式镜头聚焦；尚未交付 Android APK、视频导出、自动战斗编排、转场或对话自动相向／对视。几何及浏览器检查不代替用户对动作观感、声音质量和移动设备性能的验收。
