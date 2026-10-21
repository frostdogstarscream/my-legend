# 演绎页重建素材

来源为用户发送的一张页面截图：`C:/Users/83895/AppData/Local/Temp/codex-clipboard-41e0e99c-7ad0-4023-9c95-d960bb811414.png`。完整原图副本位于 `reference.png`，只用于比对，没有作为运行时界面图片。

下列资产使用内置 Image Gen，按截图中的场景、成年角色外观及构图重建。属于生成的近似素材，面孔、衣服细节及场景细节可能与原作不同；不是原视频帧或透明原图。没有新生成视频或音频。

| 文件 | 尺寸与用途 | 调用提示词 |
|---|---|---|
| `stage.png` | 1672 × 941，16:9 场景海报，包含三名成年角色 | `prompt-stage.txt` |
| `shenyan.png` | 1122 × 1402 RGBA，沈砚透明半身像 | `prompt-shenyan.txt` |
| `sutang.png` | 1122 × 1402 RGBA，苏棠透明半身像 | `prompt-sutang.txt` |
| `laoqin.png` | 1122 × 1402 RGBA，老秦透明半身像 | `prompt-laoqin.txt` |

三个人像均有透明 alpha 通道；卡片以 contain/FIT 显示，保持人物完整，卡片及圆角在界面层独立裁切。背景海报按 16:9 显示。按钮、字幕、角色名、进度和图标不在这些生成图片里。

所有提示词文件记录实际生成调用的完整 prompt、参考路径与透明背景参数。生成源文件路径如下：

- 场景：`C:/Users/83895/.codex/generated_images/01a139de-a77d-7af2-aa1c-b8ad160173b4/exec-a2ec7c9c-620e-4f11-b83c-6b9a5f415c77.png`
- 沈砚：`C:/Users/83895/.codex/generated_images/01a139de-e3ff-7830-b20b-a95ca6ee5626/exec-bd646d0b-0919-43c7-aaac-297215753596.png`
- 苏棠：`C:/Users/83895/.codex/generated_images/01a139df-1b84-79f2-b140-4acecbf19510/exec-ad22ecc3-709e-40b2-bf7e-a48b2edff607.png`
- 老秦：`C:/Users/83895/.codex/generated_images/01a139df-1b84-79f2-b140-4acecbf19510/exec-6e0cec00-31a9-4c3e-b5e7-b8fa348ace96.png`

截图可确认的是角色名、老秦原句、当前 28.0 秒与总时长 34.1 秒。原型中另外两句对白及分段边界是交互示例。


本目录保留原生成记录；本版本运行时素材位于 `../../src/static/performance`，产品显示名已改为“我的传说”。
