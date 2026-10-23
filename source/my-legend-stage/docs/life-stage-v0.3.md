# 少女前线素材动态演绎 v0.3

本轮为本机 H5 技术验证。实施基线是已有 Spine 2.1／Pixi 战斗预览、真实 AudioMix／MeasuredTimeline 和 HTMLAudioElement 播放器。生活演绎扩展同一舞台包和预览 API；原下载目录的 v0.2 文档与游戏素材保持原样。

## 使用与交付范围

1. 按现有说明准备固定版本运行时，运行 `runtime/my-legend-story/.venv/Scripts/python.exe ops/my-legend/prepare_life_preview.py`。
2. 运行 `ops/my-legend/Start-SpinePreview.ps1`，打开 `http://127.0.0.1:5193/#/pages/performance/index?mode=spine&profile=life_v1`。
3. 未登录时先进入本机创作。页面的“素材试演”展示三名演员，可选择单个动作、服装、循环或单次模式。
4. 选择已交付音频的成品，按剧情角色绑定素材，选择指挥官初始服装，生成关键词编排。
5. 在简表修改、禁用、新增指令；点击“应用并预览”校验并保存。JSON 导入先通过服务端校验进入草稿，应用后才替换当前舞台。
6. “生成技术覆盖草稿”专门覆盖生活动作、移动、22 秒换装及镜头检查。该草稿显式标记 `technical_coverage`，不代表剧情自动识别。
7. 使用保存版本链接或完整哈希恢复。重新自动编排生成新的内容版本，旧文件保留；相同输入的重复保存复用相同哈希。

首版最多三个舞台实例，不含嘴型、独立情绪附件、家具交互、APK／小程序、视频导出或素材上传。关键词规则不调用模型，也不触发故事／音频生成 Worker。

## 素材、版本与 QA

| 素材 | 源索引 ID | 配对与能力 |
|---|---|---|
| AK15Mod 生活骨骼 | `8387aa75c64f` | `RAK15Mod.skel` 配 `AK15Mod.atlas/png`；wait、move、sit、pick、lying |
| HK416 生活骨骼 | `84f5424146b7` | `RHK416.skel/atlas/png`；wait、move、sit、pick、lying |
| 女款初始服装 | `16a77e714e31`、`afd5df2b98bc`、`2c5c09efd9bc` | head、body、foot 三部件；wait、move、sit、victory、victoryloop |
| 女款夏装 | `809892edabef`、`6cc8c910903f`、`a1b843f6d758` | head、body、foot 三部件；wait、move、sit、victory、victoryloop |

导入器读取游戏目录的准确索引，不按文件名前缀重新猜配对。检查源目录边界、原始 QA 标记、骨骼解析、图集页面、PNG 尺寸、附件名称引用、必需动作及三部件共同动作。输出包括原二进制、转换 JSON、Atlas、PNG 和固定运行时的内容引用。

目录位于忽略的 `runtime/my-legend-spine/catalogs/<sha256>.json`，当前生活目录指针为 `life-current.json`，内容文件位于 `content/<sha256>`。旧战斗 `catalog.json` 不覆盖。资源接口仅允许已登记的文件，并在服务端和客户端校验大小、SHA-256；保留的目录快照继续可读。

Actor 使用 `legend_actor/0.3`，Outfit 使用 `legend_outfit/0.3`；清单包含版本、能力、资源引用及校准版本。区分素材 `actor_id`、舞台 `instance_id` 和剧情 `role_id`。`ready` 表示资源结构与能力 QA 通过，不等同于人工观感验收。

三部件使用工坊的 Hair／LHand／Foot／Body／Face／RHand／FrontHair 插槽带跨部件排序；保留按原动画名覆盖的校准。初始服装待机姿态确定统一缩放与脚底参考，其他动作和服装沿用该参考，防止每帧重新缩放。所有可选服装预加载成功后才替换舞台。

## 接口与编排协议

| 接口 | 行为 |
|---|---|
| `GET /api/v1/stage-preview/catalog` | 不带 profile 保留战斗目录；`profile=life_v1` 返回 `spine_life_catalog/2.0`；可传 `catalog_sha256` 读取保留快照 |
| `GET /api/v1/stage-preview/assets/{sha256}` | 登录后读取显式目录允许的内容文件 |
| `POST /api/v1/production-jobs/{job_id}/stage-preview` | 旧请求仍返回战斗预览；生活请求指定 `profile: life_v1`，`operation: compile` 或 `validate_and_save` |
| `GET /api/v1/production-jobs/{job_id}/stage-preview/{preview_sha256}` | 校验任务归属、预览内容哈希和原成品引用后恢复不可变版本 |

`compile` 接收 `bindings: {role_id: actor_id}` 与 `initial_outfits: {role_id: outfit_id}`。默认按首次发言顺序分配首批三个演员。旁白不占演员位，未绑定角色保留声音与字幕；简表配置至少绑定一个演员。多个舞台实例可以共享同一个素材演员。

`validate_and_save` 接收完整 `script`。成功返回 `spine_stage_preview/2.0`，包括原 script／audio／timeline 的 ArtifactRef、角色清单、诊断、StageScript 和预览哈希。预览位于 `previews/<job_id>/<hash>.json`；不写原任务、交付物和生产状态。

StageScript 使用 `legend_stage_script/0.3`。绑定 job、剧情版本、音频哈希、时间轴哈希、目录哈希、规则版本、sample_rate、total_frames。字幕与舞台 cues 分开；字幕来自原成品，禁止通过编排编辑改写。来源统一为 `episode_id/scene_id/line_id` 三元组。

每条 cue 包含 `id/type/instance_id/start_frame/end_frame/enabled/params/source/source_text/rule_id/reason`。

| type | params | 时间 |
|---|---|---|
| `body.play` | `action`、`mode: once/loop/hold` | 正区间 |
| `actor.move_to` | `to: [x,y]`、`visible_after` | 正区间 |
| `actor.place` | `position: [x,y]`、`visible`、`facing: -1/1` | 瞬时 |
| `actor.change_outfit` | `outfit_id` | 瞬时 |
| `camera.focus` | `target_instance_id` | 正区间 |

数值坐标为 0–1 的有限值。时间是音频采样帧，不能当作视频帧；采样率取真实 AudioMix。区间为 `[start,end)`，瞬时事件从指定采样帧起持续生效。disabled 指令保留草稿数据但不执行；结构、角色、来源和基本时间仍须合法。

最终脚本中，同一演员的身体动作与移动不能重叠；镜头只有一条活动轨道；同类瞬时指令不能在同一演员同一时刻冲突。移动中途不允许重设位置。同时刻先换装、再定位、再采样动作；逐个检查动作区间经过的全部服装能力。失败返回 422 及定位到指令的诊断，保留旧有效舞台。

## 时间求值与规则约定

`evaluateStage(script, sampleFrame)` 是无历史状态的纯函数。每次从初始位置、服装和可见性推导当前位置，移动终点保留；离场完成后隐藏，倒回之前恢复可见。身体模式：once 播放完原动作后回待机，loop 在区间内循环，hold 播放完后保持末姿态至区间结束；任何区间结束后均回待机。原骨骼事件不外放声音。

音频演绎唯一时钟为 `HTMLAudioElement.currentTime`。暂停、seek、倍速和重播都按绝对时间求值；素材试演才使用独立预览时钟。说话提示使用颜色变暗，避免降低整体透明度导致头发、脸部穿透。镜头聚焦不改变角色逻辑坐标，无活动指令时恢复全景。

规则版本为 `zh_life_keywords/1`，处理动作行、旁白和对白，覆盖待机、行走、入场、离场、靠近、远离、坐下、起身、拾取、躺卧。词组／同义词在编译器中显式维护，不做单字动作匹配。起身以当前素材的站立待机姿态表达，没有虚构新的起身动画。

- 明确姓名和唯一别名优先；同一句内可沿用已明确主体，不能跨句猜测；对白“我”绑定说话者。无法解析的代词、多主体和目标产生诊断。
- 否定、禁止、疑问、条件、将来意图与转述按显式规则排除。规则不保证完整中文语义理解，误判通过简表修正。
- 自动指令锚定来源行／操作区间，不声明逐词对齐；瞬时标记按原动作时长安排。多动作按文本顺序，超出来源区间或成品结尾裁剪并报告。
- 冲突优先级为动作行、旁白、对白；同级较早指令优先，冲突指令跳过并报告。未定位到时间轴、未绑定演员、不支持能力不静默执行。
- 自动移动限制边界、目标距离及占用路径；无法确定目标物的位置时报告，不把家具词假装映射成演员。
- 服装只由初始选择和显式换装指令控制。指挥官不具备 pick／lying，不回退成待机冒充成功。

## 验证与证据

运行前端 type-check、Vitest、H5 build；运行后端 `test_life_preview.py`、`test_spine_preview.py`、`test_production_playback.py`。Windows 上为 pytest 指定运行目录下的新 `--basetemp`，避免系统临时目录权限影响测试。保留原战斗数学检查。

浏览器检查覆盖：全部首批动作与服装、网格有限性／UV／索引、反向跳转姿态、至少二十次音频跳转、换装边界、暂停和倍速、重播、编辑禁用、无效草稿、资源失败保留旧舞台、目录失败后的音频字幕、JSON 往返、保存版本恢复。使用真实成品前后比较任务内容与交付哈希。

本轮具体结果见 `life-acceptance-20261022.md`。所有截图、测量和短时性能数据只代表记录中的本机环境；用户对动作观感、规则准确性和真实后台切换的验收单列，不以结构测试替代。
