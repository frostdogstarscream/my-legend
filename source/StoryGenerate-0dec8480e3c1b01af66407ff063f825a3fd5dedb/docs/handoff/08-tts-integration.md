# 08 TTS 团队联调手册

StoryDelivery是完整故事、角色快照、版本谱系和结构化台本；RenderManifest是从READY故事无损投影的**逐句演绎任务队列**。建议TTS消费manifest，仍保留StoryDelivery供上下文、资源和审阅使用。实际合成平台、音频拼接和播放器由其他团队实现。

真实完整例：[四集RenderManifest HTTP响应](../../6-实测产物/phase11_live_20261015_v3/continue/render.json)的response字段。请求：GET `/v1/story-versions/1cf88eb73f914da89b058679166830b6/render-manifest`；无旧库权限时直接读JSON。本文字段与[downstream.py](../../4-引擎代码/drama_engine/service/downstream.py)对应，全部精确类型见[OpenAPI](contracts/openapi.json)的RenderManifest/RenderTask/Utterance。

|字段|含义及消费规则|
|---|---|
|contract_version / delivery_format_version|分别为manifest及故事契约版本；不认识的版本应拒绝/协商，不静默丢字段|
|story_version_id|不可变来源版本；与合成产物建立追踪关系|
|role_map|本故事角色、旁白与演绎者映射；与character_snapshot核对|
|tasks[].sequence|1起连续、全局排序；跨集按数组顺序，不按字符串ID排序|
|episode_id/index, scene_id, line_id|源台本定位；保留用于回放和问题定位|
|task_id|SHA256(version:episode:scene:line)，同版本稳定；不是HTTP异步大纲任务ID|
|kind / source_kind|speech对应dialogue/narration；cue对应sfx/music/action|
|speaker_role_id|对白说话人、本故事role，不是角色库character_id|
|narrator_id|旁白身份；旁白不必有speaker_role_id|
|performer_id|声音演绎路由；speech必须有，cue不保证有|
|text|原文，不包含人工补句；cue文本是效果/动作描述，不应朗读为对白|
|asset_refs|image/voice/expression/action引用，包括asset_id、revision、可选uri/performer_id；不自动下载|
|performance.emotion|逐句情绪；TTS自行映射供应商能力，记录降级|
|tone_instruction|语气自然语言要求，不应混入朗读正文|
|emphasis|span_text非空；occurrence从1起区分重复片段；可选start_char>=0、end_char>=1；strength=medium/strong，默认medium；只对原文对应片段处理|
|speech_rate / pitch / volume|speech_rate默认1.0、范围0.5..2.0；pitch/volume为可空字符串。供应商单位需显式映射，不能假设字符串直接等于其倍数/dB|
|pauses|after_char>=0、duration_ms为0..5000；按原文字符位置/毫秒停顿，不能把JSON读出来|
|delivery_note|其他演绎备注，供导演/TTS上下文|
|audio_cue_ref|关联音效引用，不是已生成音频URL|

处理流程：验证contract→验证sequence与task_id唯一→role_map/performer资源匹配→speech合成或cue路由→记录每句结果与音频→下游自行组装/验收。不得对sfx/music/action统一调用对白TTS，例如S03结尾“两人共同握住铃锤”的视觉动作被归入cue，是否补旁白或保留效果需声音团队审阅，不能让模型提示自动朗读。

缺少speech performer应拒绝并回报角色映射错误，不能随机使用其他角色音色。资产uri可空：按asset_id+revision向资产团队解析；无法解析则阻塞该句或按业务明确约定降级并记录。旁白也需有效performer；不要将narrator_id当character_id。现有URI可能只是引用，没有音频可用性保证。

排序以manifest.sequence为准；集/场景是组织边界，不是已测音频时间轴。target_duration_sec是创作目标，必须真实合成后测秒数。当前没有逐句实际start_ms/end_ms或精确毫秒拼接。pauses/emphasis的适用性、供应商参数映射和同音多角色一致性需下一轮真实TTS联调。

去重建议：同版本用task_id防重复消费。新版本哪怕继承旧台词，task_id也会变化；如要跨版本缓存，另算text+performer+全部performance+voice资源revision+供应商参数hash，并检查上下文，不能按line_id直接复用。此缓存属于下游设计建议，服务没有实现音频缓存。

模拟验证：POST `/v1/story-versions/{id}/simulate-tts`，返回`simulation_only:true`、`contract_state:SIMULATION_VERIFIED`、speech_count、cue_count、按顺序的`simulation://task_id`占位符。真实[模拟响应](../../6-实测产物/phase11_live_20261015_v3/continue/simulation.json)证明队列可被解析，不表示有音频。

三种状态必须分别展示：`story_state:SUCCEEDED`；`audio_state:NOT_SYNTHESIZED`；`playback_state:NOT_REPORTED`。模拟后仍然如此。不要把READY或Job SUCCEEDED显示为“音频已播放”。本服务没有音频成功回写/设备播放上报HTTP接口，其他团队需要自己的持久化状态及版本关联。

下一轮交接检查：拿S03一集实际合成，验证旁白/两角色路由、重音停顿、cue资源、失败单句重试、测量时长、音频内容与版本一致、切换新版本时缓存不串音。以上尚未在本轮实施，不是验收通过项。
