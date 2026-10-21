# 下一阶段 TTS 与客户端联调

StoryDelivery 1.0 契约保持不变。`GET /v1/story-versions/{id}/render-manifest` 将完整 READY 正文转换为有序演绎任务。每项包含稳定 task_id、sequence、集/场景/行 ID、source_kind、speech/cue 分类、说话角色、performer_id、原文、完整 performance 标注及冻结资产引用。任务 ID 由版本与原行路径确定，重复导出稳定。

dialogue/narration 属于 speech；sfx/music/action 属于 cue。TTS Consumer 只合成 speech；cue 交给声音资产/混音/动作客户端，不把音效标记读成台词。逐句音频按 sequence 拼接，保留情绪、语气、重音、速度、音高、音量、停顿和 note；第三方不支持某标注时显式记录降级，不能默默丢字段。声音资源 URI 是外部资产引用，本轮不下载或验证素材质量。

`POST /v1/story-versions/{id}/simulate-tts` 运行契约 Consumer，校验每项可路由、稳定 ID、顺序与必需标注，输出 simulation_only=true、SIMULATION_VERIFIED 和拼接占位结果。simulation URI 不是音频文件。

三种状态独立：故事任务 SUCCEEDED 表示正文完成；当前 audio_state 始终 NOT_SYNTHESIZED，playback_state 始终 NOT_REPORTED。真实 TTS/设备联调应在独立业务服务记录逐任务供应商请求 ID、音频 URI/hash、失败与重试、合成完成和设备播放回执，不改变不可变正文或虚构成功。

建议下一轮先用实测目录的 render 与 simulation JSON 做供应商适配，按 task_id 幂等处理、限定并发、记录供应商 Token/字符费用；再接 App 任务轮询和音频列表；最后接硬件播放回执。当前服务接口保留这些边界，没有真实 TTS、硬件控制或前端页面。
