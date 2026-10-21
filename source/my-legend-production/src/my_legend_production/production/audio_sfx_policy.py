"""Versioned acceptance policy for approximate, source-bound MVP sound design."""

SFX_POLICY = {
    'version': 'pragmatic_sfx/1',
    'scope': 'sound_design_fidelity_only',
    'nonblocking_rule': 'acoustic_detail_simplified',
    'allowed_simplifications': [
        '同一声源的精确加速、节奏、重复次数可用代表性音效近似',
        '远近、方位、混响、多普勒可省略或用简单音量变化近似',
        '有限的复合声景可用一份完整素材，不强制按每个形容词拆分',
        '非剧情关键的细小装饰声可省略，但该行的主要声音必须保留',
    ],
    'blocking_requirements': [
        '完整台词和旁白、角色身份及原文来源绑定',
        '主要声源和推动剧情的声音线索、明确的发生与停止顺序',
        '不得以旧声源的增益变化冒充新声源',
        '合法锚点、无循环、实测区间有效、对白不重叠或被截断',
    ],
    'human_listening': 'pending',
}

SFX_POLICY_PROMPT = '''本轮采用 pragmatic_sfx/1 的简化音效验收标准。
优先保证可理解的剧情与主要声源，不追求电影级声学细节。能用一个完整 one_shot 表现的有限过程优先一次生成，连续氛围才用 background。
脚步由慢变快可保留同类脚步素材而省略精确加速，敲击可省略精确节奏/次数；远近方位、混响、多普勒可简化为普通声源或简单淡入淡出。
这类近似必须如实说明，不声称音量实际改变了节奏。若节奏/次数本身是密码、线索或决定剧情结果，则仍必须保留其关键含义。
同一有限声景可以合成一份素材；非关键的灯火微响等装饰声可省略，不要求每个微小细节单独生成。主要声音不能全部省掉，也不能把降低风雨声说成新出现的呼吸声。
原文和 cue_text 的来源绑定不变。编排在 reason 中说明实际保留的声音与省略的细节；审核在 advisories 中记录允许的简化。
只有声学细节精度不足、而主要声音已经保留时，使用非阻断规则 acoustic_detail_simplified；关键声源缺失、错误对象、剧情线索遗漏和时序矛盾仍为阻断问题。
不得仅因 one_shot 无子时间轴就推断它不能包含重复敲击、驶过、开关门等完整过程；素材能否实际听清仍待听审。
'''
