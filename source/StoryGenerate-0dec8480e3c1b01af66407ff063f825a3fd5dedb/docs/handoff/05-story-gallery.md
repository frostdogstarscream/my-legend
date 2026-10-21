# 05 真实故事与结构化输出展示

这里的7份台本全部来自已归档真实Provider结果。**正文未人工润色、未拼凑**；新增Markdown只提供标题、角色/ID标识和原文排版。脚本按结构化JSON逐条输出所有集、场景、对白、旁白、音效等内容，并保留角色与连续状态。每篇有真实输入、批准大纲、版本、调用证据、Token/模型/耗时链接；演绎标注完整值在原JSON/RenderManifest中。

|样例|用途|全文|结构化来源|
|---|---|---|---|
|S01《老街回音》|Phase11 Create，两集未兑现铃声伏笔|[完整台本](samples/S01.md)|[原始HTTP delivery](../../6-实测产物/phase11_live_20261015_v3/create/delivery.json)|
|S02《老街回音》|同故事一个场景的决策方式修改；与S01对照|[完整台本](samples/S02.md)|[原始HTTP delivery](../../6-实测产物/phase11_live_20261015_v3/revise/delivery.json)|
|S03《老街回音》|新增两集、完整四集、三声铃回收|[完整台本](samples/S03.md)|[原始HTTP delivery](../../6-实测产物/phase11_live_20261015_v3/continue/delivery.json)|
|S04《老街回音：未完成的告别》|人为隐瞒故障的平行二创，改变结局|[完整台本](samples/S04.md)|[原始HTTP delivery](../../6-实测产物/phase11_live_20261015_v3/remix/delivery.json)|
|S05《老街的三声铃》|Phase10原创，台词精确修改的来源|[完整台本](samples/S05.md)|[StoryDelivery](../../6-实测产物/operations_live_v8_20261014/round_1_create/delivery.json)|
|S06《老街的三声铃》|S05的一条台词口语化，其他内容保留|[完整台本](samples/S06.md)|[StoryDelivery](../../6-实测产物/operations_live_v8_20261014/special_line/delivery.json)|
|S07《老街的三声铃》|再次续写后的完整五集；来源为Phase10原续写链|[完整台本](samples/S07.md)|[StoryDelivery](../../6-实测产物/operations_live_v8_20261014/special_continue_again/delivery.json)|

S01–S04是同一业务链。S05–S06为另一个来源及其台词分支；S07的parent不是S06，而是Phase10主链四集续写版本，version_no9不代表紧邻parent版本号。完整来源ID及内容SHA256见[机器索引](samples/index.json)。按物理call_id去重统计，各样例数字可能包含同资源失败重试，不应把7篇成本直接相加为新批次成本。

真实用户请求中林岚为维修员、周野为社区广播主持人，背景为老街广播站。原版设备接头进水，修复后约定“三声铃”；Revise改为先共同检查；Continue继承人物、关系和故障并回收约定；Remix把故障改为周野人为隐瞒、结尾公开道歉合作整改。不是只替换题目。

下游可直接查看[S03完整RenderManifest](../../6-实测产物/phase11_live_20261015_v3/continue/render.json)（HTTP包装的response字段）、[角色映射](../../6-实测产物/phase11_live_20261015_v3/continue/characters.json)、[模拟消费](../../6-实测产物/phase11_live_20261015_v3/continue/simulation.json)。逐句有emotion、tone_instruction、emphasis等，不提供真实音频。样例总数7，但集中于同一广播站母题，不能冒充7种独立题材或儿童故事泛化验收。

离线重建全文，不调用模型：`python tools/handoff/build_samples.py`。复核原则：JSON才是原始结构化证据；原始模型响应在每篇的raw目录中。Markdown不对缺陷进行润色补救，质量问题留在[06报告](06-quality-cost.md)中。
