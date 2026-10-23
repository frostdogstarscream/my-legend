# 生活演绎验证记录 · 2026-10-22

实施范围：v0.3 本机 H5、首批生活素材、指挥官三部件换装、关键词规则、编排简表和不可变预览版本。本记录为技术验证，不代表用户观感或规则准确性验收。

## 可打开的结果

- 素材试演：`http://127.0.0.1:5193/#/pages/performance/index?mode=spine&profile=life_v1`
- 真实成品技术覆盖：`http://127.0.0.1:5193/#/pages/performance/index?mode=spine&profile=life_v1&productionId=a47ca0f4-9434-4d7e-81ef-b12bf759a1c7&previewId=1bb758a3abf6bacd606d2aafc71844ce61d9ab21472b197b08edfc7b36378e10`
- 该成品有两个声音角色。验证时分别绑定 AK15Mod 和女指挥官，以检查真实音频上的生活动作和换装；HK416 通过三演员素材试演与几何检查验证。
- 技术覆盖样例在 22 秒切换夏装，24–27 秒坐下。它保留真实音频和字幕，额外动作明确标记为技术覆盖，不声明来自原剧情。

## 已通过

| 检查 | 结果 |
|---|---|
| 资源导入 | 8 个骨骼、3 个演员、2 套服装；Spine 2.1.27；图集、贴图尺寸与附件配对通过 |
| 全部生活动作 | 20 个动作／服装组合，620 个采样点；骨骼与网格有限、UV／索引有效，反向采样一致 |
| 真实音频 | 77.84 秒，48 kHz；原 AudioMix 与 MeasuredTimeline 保留 |
| 回放 | 24 次前后定位，包括换装前、换装时、换装后；重复时刻姿态哈希完全一致 |
| 暂停、倍速、重播 | 暂停姿态稳定；0.5／1／1.5／2 倍速均推进；本次同帧采样画面与音频位置差约 2–7 ms；末尾重播恢复开头 |
| 编辑与保存 | 无效时间区间返回 422，旧舞台与版本保留；禁用后保存、旧版本恢复、JSON 导出导入通过 |
| 资源错误 | 阻断素材请求时旧有效舞台保留；阻断目录请求后仍可播放音频并显示原字幕；恢复请求后保存链接可重新打开 |
| 原数据 | 任务内容哈希验证前后相同，未改写原音频、剧本或时间轴 |
| 自动化回归 | 前端 72 项、后端预览／播放相关 52 项通过；TypeScript 检查与 H5 构建通过 |
| 旧战斗回归 | HK416MOD／MG4／G11MOD 共 20 动作、620 个数学采样点通过 |

环境：Windows、Chrome 149、ANGLE Direct3D11、NVIDIA GeForce RTX 5060 Laptop GPU，逻辑画布 960×540。三演员额外验证舞台的 120 帧短采样约 89 FPS，纹理数 8。此数据不是长时间稳定性或移动设备性能承诺。

## 边界与待人工复核

- 自动化浏览器切换新标签时没有发出 `visibilitychange`，因此真实后台切换暂停 **未被本次自动化证实**。合成 hidden 可见性事件已验证会暂停播放；还需在正常使用的浏览器中切换标签／最小化后复核。
- 动作观感、三部件衔接与自动编排准确性需要用户确认；当前 `human_acceptance=false`。
- 关键词规则无法理解所有中文语境。无法唯一确定主体、目标或素材能力时报告诊断，允许简表修正；不把关键词匹配描述为完整剧情理解。
- 构建仍有既有的三个字体资源解析警告。故障注入中的 422、资源中断为预期测试；另有既有 favicon 404。最终正常恢复页面无运行错误。
- pytest 的 FastAPI TestClient 所有权检查在沙箱环境阻塞；在本机权限环境重跑全部相关 52 项后通过。未据沙箱半程输出判定通过。

## 证据位置

相对仓库根目录：

- `runtime/my-legend-spine/life-verification-20261022.json`：汇总和明确的验收边界。
- `runtime/my-legend-spine/life-audio-verification-20261022.json`：24 次跳转哈希、倍速、编辑、保存及原任务不变。
- `runtime/my-legend-spine/life-material-verification-20261022.json`：全部素材动作、620 采样及环境信息。
- `runtime/my-legend-spine/life-failure-verification-20261022.json`：故障注入、合成可见性事件及恢复。
- `output/playwright/life-real-summer-25s.png`：真实音频 25 秒夏装坐下画面。
- `output/playwright/life-summer-sit.png`：三演员素材试演中的夏装坐姿。
- `output/playwright/life-audio-fallback.png`：目录失败后的音频与字幕。
- `output/playwright/life-coverage-export.json`：页面导出的真实技术覆盖脚本。

素材目录哈希：`5efadb7b264aa037deb130839425be86f8660ca3f984d9ac60cb02a0bc142b92`。

AudioMix：`3b0af25166c17b642928dfea9a999c64449cef952dba683adb14c1719a609761`。

MeasuredTimeline：`93f8dea171924e18b6c21c859aad91ef737ec3f0c69d39a73ff7356d0b6e3e59`。
