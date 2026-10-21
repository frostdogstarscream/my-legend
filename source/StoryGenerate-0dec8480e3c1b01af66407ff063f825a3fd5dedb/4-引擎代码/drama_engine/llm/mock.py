"""离线 Mock 大模型。

它的用途不只是"让代码跑起来"：它是**校验器与修复回路的测试替身**。
四种注入模式，对应四件必须被验证的事：

  inject_violations=True  结构层缺陷：金手指层级冗余（K17）、视觉描写（R10）、台词超长（R12）、
                          行为卡缺字段（CH01–CH06）、关系边缺转折点（RL01–RL04）、
                          账本自相矛盾（FC01–FC04）
                          → 验证 Tier-1 能抓到，且分类路由到正确的修复节点
  inject_tier2=True       语义层缺陷：结构完全合规，但把"上一次成功的副作用"这条因果线抹掉
                          → 验证 Tier-2 语义裁判确实在工作
  inject_fakes=True       声明与正文脱节：结构全绿、账本合规，大纲声明了副作用来源 /
                          反转 / 角色选择 / 关系转折 / 量化成果，**但正文不兑现它们**
                          → 验证证据评审层：静态层抓标签空转与假反转，语义层抓假因果与假信任
  两者都关                 干净路径 → 验证整图能顺利收敛

修复节点拿到具体违规后重问，第 1 次尝试输出合规内容。
这样"两级校验 → 分类路由 → 定点修复 → 复检"整条链路是被真正走过的。

## 一个刻意的设计：Mock 会主动"兑现"它声明过的东西

inject_fakes 关掉时，Mock 生成正文时会读取 continuity 切片，为每一条声明插入一条**兑现行**：
账目被提及、选择被执行、关系转折发生、数字出现。
这模拟的是一个负责任的创作者行为，也让干净路径能真正通过证据评审 ——
否则"声明与正文脱节"会变成默认状态，证据层就永远只有误报，无法验证。
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from ..schemas import (
    Beat,
    BehaviorBible,
    CastDraft,
    Episode,
    EpisodePlan,
    FactLedger,
    GadgetSpec,
    JudgeResponse,
    Line,
    LineRewrites,
    OutlineDraft,
    ProseParagraph,
    ProseStory,
    TopicChoice,
)
from .base import BaseLLMProvider, LLMResult, LLMSpec, estimate_tokens

# 故意注入的缺陷素材
DEFAULT_VISUAL_LEAK = "他嘴角微微一勾"
DEFAULT_LONG_LINE = "我昨天刚从县医院拿到的那份化验报告现在就摆在你面前的这张桌子上"

# 兑现行的标记词。Mock 的裁判用它们做可运行的代理判据 ——
# 这些标记词不是"正确答案"，而是"这条声明在正文里确实有载体"的可检索证据。
MARK_CAUSALITY = "都是他救过的人。"
MARK_CHOICE = "这条命我押上去了。"
MARK_RELATION = "他把甲卸下来放在案上。"
MARK_NEW_INFO = "他们动了粮道。"
LEAK_LINE = "这些青霉素总得有个来处。"

_DIGIT_RE = re.compile(r"[0-9０-９]|[一二三四五六七八九十百千万两半]")

# 「专业知识轨」的判定词。判据只有一处定义 —— 它同时被"猜资产"和"补齐资产"用到，
# 两处各写一份，改一处就会出现"补进来的资产自己不算知识轨"这类自相矛盾。
_KNOWLEDGE_MARKERS = ("青霉素", "抗生素", "疫苗", "针", "药")

# 物资类候选（用于补齐第二件资产）。判据同上：不在 _KNOWLEDGE_MARKERS 里即为纯物资。
_MATERIAL_FALLBACK = "现代食品"
_KNOWLEDGE_FALLBACK = "现代药品"


class MockLLMProvider(BaseLLMProvider):
    name = "mock"

    def __init__(self, inject_violations: bool = True, inject_tier2: bool = False,
                 inject_fakes: bool = False) -> None:
        self.inject_violations = inject_violations
        self.inject_tier2 = inject_tier2
        self.inject_fakes = inject_fakes
        self.call_log: list[dict] = []

    # ------------------------------------------------------------------ 自由文本
    def complete(self, spec: LLMSpec, system: str, user: str) -> LLMResult:
        text = f"[mock:{spec.role}] {user[:80]}"
        self.call_log.append({"role": spec.role, "structured": False})
        return LLMResult(
            text=text, model=spec.model,
            input_tokens=estimate_tokens(system + user),
            output_tokens=estimate_tokens(text),
        )

    # ------------------------------------------------------------------ 结构化
    def complete_structured(self, spec: LLMSpec, system: str, user: str, schema: type):
        t0 = time.monotonic()
        self.call_log.append({"role": spec.role, "structured": True, "schema": schema.__name__})
        ctx: dict[str, Any] = dict(spec.extra or {})
        attempt: int = int(ctx.get("attempt", 0))
        # 结构缺陷只在第 0 轮注入：修复节点重问时给出合规内容，
        # 这样"修复 → 复检"这条路是真的走通的，而不是靠关闭校验蒙混过关。
        dirty = self.inject_violations and attempt == 0
        ctx["_tier2"] = self.inject_tier2
        ctx["_fakes"] = self.inject_fakes

        builder = {
            TopicChoice: self._topic,
            GadgetSpec: self._gadget,
            CastDraft: self._cast,
            BehaviorBible: self._behavior,
            OutlineDraft: self._outline,
            FactLedger: self._ledger,
            Episode: self._episode,
            EpisodePlan: self._episode_plan,
            JudgeResponse: self._judge,
            LineRewrites: self._line_rewrites,
            ProseStory: self._prose_story,
        }.get(schema)
        if builder is None:
            raise NotImplementedError(f"Mock 未实现该 Schema: {schema.__name__}")
        result = schema.model_validate(builder(ctx, dirty, user))

        # Phase 4: telemetry trace
        latency_ms = int((time.monotonic() - t0) * 1000)
        self._trace_llm_call(spec, LLMResult(
            text="[mock]", model=spec.model,
            input_tokens=estimate_tokens(system + user),
            output_tokens=estimate_tokens(user),  # rough
        ), latency_ms)

        return result

    # ------------------------------------------------------------------ 选题
    def _topic(self, ctx: dict, dirty: bool, user: str) -> dict:
        # 只在**创意本身**里找关键词，不在整段提示词里找。
        # 踩过的坑：整段提示词包含赛道菜单，而菜单里有「脑洞穿越搞事业」「重生虐恋」，
        # 于是任何创意都会命中"穿越/重生"被判成 G08 —— 判据被候选列表污染了。
        # 判据必须只来自被判断的对象本身。
        idea = self._idea_of(user)
        markers = ("穿越", "古代", "重生", "系统", "金手指", "朝", "丞相", "北伐", "青霉素", "军粮")
        if any(m in idea for m in markers):
            return {
                "genre_id": "G08", "recipe_id": "R3",
                "rationale": "该命题是降维搞事业型，戏剧张力在『现代手段与古代常识的碰撞』里，"
                             "可全部由台词与音效承载。音频适配 62 偏低，因此必须靠音效密度与"
                             "角色合并来补偿 —— 这个取舍本身需要被记录，而不是被静默改掉。",
            }
        return {
            "genre_id": "G01", "recipe_id": "R4",
            "rationale": "该命题是现实家庭冲突型，张力天然存在于台词里，音频化后不会出现观感断崖。",
        }

    @staticmethod
    def _idea_of(user: str) -> str:
        """从提示词里取回"创意"本体（格式见 prompts.topic_select / gadget_design）。

        Mock 的判据必须只来自被判断的对象本身。之前两处判据都是拿整段提示词去匹配，
        而提示词里既有赛道菜单（含『穿越』『重生』）又有规则库示例（含『青霉素』『军粮』），
        结果是"判据被候选列表污染"—— 任何创意都得到同一个答案。
        """
        head = user.split("创意：", 1)[-1]
        return head.split("\n", 1)[0].strip()

    # ------------------------------------------------------------------ 金手指
    def _gadget(self, ctx: dict, dirty: bool, user: str) -> dict:
        # 同样只在创意本体里猜资产。整段提示词里含规则库的示例（『青霉素』『军粮』
        # 写在穿越规则库的说明里），照整段匹配会让任何一个创意都拿到同一套金手指。
        locked: list[str] = list(ctx.get("locked_assets") or []) or self._guess_assets(
            self._idea_of(user)
        )
        # 补齐到"两轨齐全"。两个判据都会因为单件资产而挂：
        #   K02 金手指必须有专业知识轨 —— 只有物资轨的主角会退化为搬运工；
        #   价值层级分工要求 L1 与 L2 各占一层。
        # 补齐只看资产名，不看模型怎么想 —— 判据必须由引擎掌握，不能靠模型自觉。
        if not any(m in n for n in locked for m in _KNOWLEDGE_MARKERS):
            locked = [_KNOWLEDGE_FALLBACK] + locked
        if len(locked) < 2:
            locked = locked + [_MATERIAL_FALLBACK]
        locked = locked[:2]

        assets: list[dict] = []
        for idx, name in enumerate(locked[:2]):
            medicine = any(k in name for k in _KNOWLEDGE_MARKERS)
            assets.append({
                "id": f"AS{idx + 1}",
                "name": name,
                # 干净路径下，医疗类落在生存层、其余落在社会层 —— 这正是 K17 要的分工
                "layer": "L1" if (dirty or medicine) else "L2",
                "curve": "TC1" if medicine else "TC2",
                "capacity": "infinite",
                "activation": f"随身携带{name}，随时可取，不需要任何前置条件",
                "knowledge_track": medicine,
                "non_resource_constraint": None if dirty else (
                    "信任与知识盲区：古代无法做皮试，每次用药都是赌博；且无人相信他有资格决定用药"
                ),
                "decay_plan": None if medicine else (
                    "全剧定点投放 8 次，每次引入新对象或新用途；最后一次是主角自己尝不出味道"
                ),
            })

        if dirty:
            # 把两项都压到生存层 —— 即"一个治伤、一个管饭"那个历史上真实犯过的错误
            for a in assets:
                a["layer"] = "L1"
        elif len(assets) == 2 and assets[0]["layer"] == assets[1]["layer"]:
            assets[1]["layer"] = "L2"

        return {
            "assets": assets,
            "core_conflict": "北伐能否打赢（伤病与补给）",
            "escalated_conflict": None if dirty else (
                "主帅的政治处境：他每赢一次，同僚的处境就差一分，因此他必须被除掉"
            ),
            "escalation_rationale": "" if dirty else (
                "两项金手指同时取消了战争的『伤』与『饿』，军事冲突的难度被整体抹平，"
                "必须把冲突上移到政治层才能重建张力（K16）"
            ),
        }

    def _guess_assets(self, user: str) -> list[str]:
        return [n for n in ("青霉素", "肯德基", "军粮", "手机", "打火机", "抗生素", "玻璃珠", "可乐")
                if n in user]

    # ------------------------------------------------------------------ 角色
    def _cast(self, ctx: dict, dirty: bool, user: str) -> dict:
        names = {"A": "周牧", "B": "随身仓库", "C": "裴肃", "D": "崔敬", "E": "赵老医官"}
        specs = [
            ("A", "主角 / 现代专业身份", "急救科护士，冷静、语速偏快", "短促的鼻息，说话前先停顿半拍", 1.15, "先救人，再算账", "急诊监护仪的长鸣", "protagonist"),
            ("B", "金手指 / 随身物资", "不开口，只有环境音与旁白", "每次启动伴随一声金属轻叩", 0.9, None, "金属轻叩", "ally"),
            ("C", "权力保护者", "中年主帅，声音低沉带沙哑", "句尾习惯性下沉，命令句多", 0.85, "此事我担着", "甲片摩擦声", "ally"),
            ("D", "同阵营反派 / 随军参赞", "世家子弟，语调上扬、爱反问", "爱拖长尾音，习惯性反问『是吗』", 1.0, "是吗", "折扇收拢的脆响", "antagonist"),
            ("E", "降维见证者", "老医官，语速慢、气不足", "说话中间有停顿，尾音发虚", 0.8, "老夫行医四十年", "药碾滚动声", "ally"),
        ]
        cards = []
        for slot, label, vlabel, vanchor, rate, phrase, sfx, faction in specs:
            cards.append({
                "slot": slot, "name": names[slot], "role_label": label,
                "voice_label": vlabel, "voice_anchor": vanchor, "speech_rate": rate,
                "catchphrase": phrase, "sfx_signature": sfx, "faction": faction,
                # dirty 时把反派写成"非同阵营"、把见证者写成不发声 —— 两个典型角色层缺陷。
                # B 槽位（随身仓库）永远不发声：它是金手指的载体，不是一个说话的人。
                # 把它算进有声角色会虚占一个稀缺名额（K15 的 5 个上限里它毫无价值）。
                "same_camp": True if slot != "D" else not dirty,
                "is_voiced": (slot not in ("B",)) and (True if slot != "E" else not dirty),
                "merged_into": None,
            })
        return {"cards": cards}

    # ------------------------------------------------------------------ 人物行为卡
    def _behavior(self, ctx: dict, dirty: bool, user: str) -> dict:
        total = int(ctx.get("total_episodes", 8))
        cast = ctx.get("cast") or []
        names = {c.slot: c.name for c in cast}
        hero = names.get("A", "周牧")
        protector = names.get("C", "裴肃")
        rival = names.get("D", "崔敬")
        witness = names.get("E", "赵老医官")

        base: dict[str, dict] = {
            "A": {
                "desire": "拿到三天时间，把第一个人救活",
                "fear": "药在自己手里，却还是救不活人",
                "misbelief": "只要救活人就能被相信",
                "boundary": "绝不拿活人试药",
                "tell": "说话前那半拍停顿突然消失了",
                "arc": "从信技术到信人",
                "choices": [
                    ("P5", "期限只剩最后一夜时", "押上自己的军令状换时间",
                     "把自己唯一的退路交出去", True, "台词：三天没好，我陪葬"),
                    ("P3", "被人当众质疑来历", "当场做给他们看，一句不辩解",
                     "把仅有的那次机会一次用掉", False, "动作音：水沸声压过质疑"),
                    ("P6", "药不够、必须放弃一个人时", "选择救那个最不值得救的人",
                     "从此失去主帅的全额信任", True, "三秒沉默后被中断的台词"),
                    ("P2", "有人出高价买药时", "把药给了付不起钱的人",
                     "得罪整个粮草系统", False, "台词：先救人"),
                ],
            },
            "C": {
                "desire": "保住这支军队的元气",
                "fear": "背上擅权自专的名声",
                "misbelief": "只要不站队就不会被清算",
                "boundary": "绝不拿士兵的命做政治筹码",
                "tell": "句尾那个下沉的尾音消失了",
                "arc": "从庇护一个人到庇护一种做法",
                "choices": [
                    ("P2", "军需与主角的请求冲突时", "先批主角的请求",
                     "被同僚记恨在案", True, "台词：照他说的做"),
                    ("P3", "被参劾的时候", "把奏章压下来，不作解释",
                     "失去向皇帝自陈的机会", False, "沉默，只有甲片摩擦声"),
                    ("P4", "旧部来求情时", "当面回绝，不多说一个字",
                     "看着旧部转身离开", False, "脚步声由近及远"),
                ],
            },
            "D": {
                "desire": "查清这个人的来历",
                "fear": "被一个没有出身的人超过",
                "misbelief": "出身决定一切，他必然是骗子",
                "boundary": "绝不在阵前动手",
                "tell": "爱拖长尾音的反问突然不拖了",
                "arc": "从夺功到自毁",
                "choices": [
                    ("P2", "功劳被分走时", "写弹劾奏章",
                     "从此没有回头路", False, "折扇收拢的脆响"),
                    ("P3", "谎言即将被揭穿时", "把谎说得更大",
                     "把自己也一起骗了进去", True, "上扬的尾音突然拔高"),
                    ("P6", "需要有人背锅时", "把责任推给部下",
                     "部下从此不再听令", False, "台词：是吗"),
                ],
            },
            "E": {
                "desire": "看清这手艺到底是真是假",
                "fear": "四十年经验被判成错的",
                "misbelief": "医道只能靠年头一点点积累",
                "boundary": "绝不替没把握的手法作证",
                "tell": "药碾滚动的声音停了",
                "arc": "从排斥到替主角作证",
                "choices": [
                    ("P5", "三天将尽而病人还没醒时", "留在帐里不走",
                     "把自己的名声押在这三天上", True, "药碾声停了一拍"),
                    ("P3", "同僚逼他表态时", "说没看懂，但要留下来看",
                     "被两边都当成墙头草", False, "台词：老夫行医四十年"),
                    ("P1", "自己被传染的时候", "先救别人，把自己排最后",
                     "把自己排到了最后一个", False, "一段发虚的尾音"),
                ],
            },
        }

        cards: list[dict] = []
        for slot, spec in base.items():
            if slot not in names:
                continue
            choices = [
                {
                    "pressure": p, "trigger": trig, "choice": ch, "cost": cost,
                    "against_self_interest": ag, "visible_as": vis,
                }
                for p, trig, ch, cost, ag, vis in spec["choices"]
            ]
            card = {
                "slot": slot, "name": names[slot],
                "desire": spec["desire"], "fear": spec["fear"],
                "misbelief": spec["misbelief"], "boundary": spec["boundary"],
                "tell": spec["tell"], "arc": spec["arc"], "choices": choices,
            }
            if dirty and slot == "A":
                # 三个典型人物层缺陷：选择过少、压力类型单一、代价缺失、无错误信念
                card["choices"] = [dict(choices[0], cost="", against_self_interest=False)]
                card["misbelief"] = ""
            if dirty:
                # 全剧没有任何"违背自身利益"的选择 —— 即"一个永远做最优解的主角"
                card["choices"] = [
                    dict(c, against_self_interest=False) for c in card["choices"]
                ]
            cards.append(card)

        if dirty:
            # 关系边只覆盖一个角色、且没有转折点 —— 关系变化退化为"时间久了就这样"
            relations: list[dict] = [{
                "a": hero, "b": protector, "axis": "trust",
                "initial": "frozen", "target": "toward", "turning_points": [],
            }]
        else:
            relations = [
                {
                    "a": hero, "b": protector, "axis": "trust",
                    "initial": "frozen", "target": "toward",
                    "turning_points": [{
                        "episode": min(3, total),
                        "event": "主帅当着全军承认是周牧救了人，把责任揽到自己身上",
                        "to": "toward",
                        "evidence_hint": "甲片摩擦声之后是一段沉默，然后他说『此事我担着』",
                    }],
                },
                {
                    "a": hero, "b": rival, "axis": "rivalry",
                    "initial": "frozen", "target": "away",
                    "turning_points": [{
                        "episode": min(2, total),
                        "event": "崔敬当众质疑周牧的来历，把折扇收拢拍了案子",
                        "to": "away",
                        "evidence_hint": "折扇收拢的脆响之后是一句上扬的反问『是吗』",
                    }],
                },
                {
                    "a": hero, "b": witness, "axis": "trust",
                    "initial": "frozen", "target": "toward",
                    "turning_points": [{
                        "episode": min(2, total),
                        "event": "老医官留了下来，看了第二个夜",
                        "to": "toward",
                        "evidence_hint": "药碾滚动声停了一拍，然后重新响起",
                    }],
                },
            ]
        return {"cards": cards, "relations": relations}

    # ------------------------------------------------------------------ 大纲
    def _outline(self, ctx: dict, dirty: bool, user: str) -> dict:
        total = int(ctx.get("target_episodes", 8))
        gadget: GadgetSpec | None = ctx.get("gadget")
        sensory = None
        if gadget:
            sensory = next((a.name for a in gadget.assets if a.curve == "TC2"), None)

        # 四幕按集数分档（而不是逐集比比例），避免小集数下某一幕被判为空
        bound1 = max(1, round(total * 0.12))            # 现代段上限 12%
        bound2 = max(bound1 + 1, round(total * 0.30))   # 落地段上限 30%
        bound3 = max(bound2 + 1, round(total * 0.85))   # 降维段上限 85%
        hero = next((c.name for c in (ctx.get("cast") or []) if c.slot == "A"), "周牧")
        protector = next((c.name for c in (ctx.get("cast") or []) if c.slot == "C"), "裴肃")

        acts: dict[int, tuple[int, str | None, int | None]] = {}
        for i in range(1, total + 1):
            if i <= bound1:
                acts[i] = (1, None, None)
            elif i <= bound2:
                acts[i] = (2, "S1", None)
            elif i <= bound3:
                loop = ["S1", "S2", "S3", "S4", "S5"][(i - bound2 - 1) % 5]
                # K06：第 5 步的难题必须源于前面某一次成功；
                # 落地段及其后一集没有"前一次成功"，属正当豁免
                side = None if (dirty or i <= bound2 + 1) else max(1, i - 2)
                acts[i] = (3, loop, side)
            else:
                acts[i] = (4, "S4", None)

        # 行为卡进入剧情的通道：大纲负责把"选择规律"安排到具体集数上。
        # 两处都不能想当然：
        #  ① 不能只安排主角 —— CH01 的判据是"每一个有声角色都要有自己的选择集"，
        #     只写主角会让其余有声角色退化成背景板（他们占着稀缺的有声名额却从不做选择）；
        #  ② 集数不够时按优先级抢占 —— 降维段是演练选择的最佳位置，
        #     其次是大结局（升维段），最后才动现代段。集数充裕时不必动前面。
        bible = ctx.get("behavior")
        voice_cards = [c for c in (getattr(bible, "cards", None) or []) if c.slot != "B"]
        slot_priority = {2: 0, 3: 0, 4: 1, 1: 2}
        candidates = sorted(
            range(1, total + 1),
            key=lambda i: (slot_priority.get(acts[i][0], 9), i),
        )[:len(voice_cards)]
        choose_ep = {i: k for k, i in enumerate(sorted(candidates))}

        # 金手指至少失效一次：降维段若存在循环，就必须有一集是"这次不靠金手指过关"。
        # 判据是"降维循环 ≥1 次则失效 ≥1 次"，所以失效点必须落在降维段内部 ——
        # 取该段最后一集，比取"第 5 集"这种绝对位置更稳（集数少时第 5 集根本不存在）。
        loop_eps = [i for i in range(1, total + 1) if acts[i][0] == 3]
        fail_ep = loop_eps[-1] if loop_eps else None

        entries = []
        for i in range(1, total + 1):
            act, loop, side = acts[i]

            exercised = None
            if not dirty and i in choose_ep and voice_cards:
                seq = choose_ep[i]
                card = voice_cards[seq % len(voice_cards)]
                choices = card.choices
                if choices:
                    ch = choices[(seq // len(voice_cards)) % len(choices)]
                    exercised = f"{card.name}@{ch.pressure}：{ch.choice}"

            entry = {
                "episode": i,
                "title": self._ep_title(i, act),
                "act": act,
                "core_goal": self._core_goal(i, act),
                "conflict_intensity": min(10, 3 + i),
                "polarity": ("negative" if (dirty and bound2 + 1 <= i <= bound2 + 6)
                             else ("positive" if i % 3 else "neutral")),
                "loop_step": loop,
                "side_effect_of": side,
                "gadget_failed": (not dirty) and i == fail_ep,
                "deployment": (f"第{i}次定点投放：{sensory}用于新对象"
                               if (sensory and i % 2 == 0) else None),
                "quantified_gain": f"救回 {i * 7} 名伤兵" if act in (2, 3) else None,
                "exercised_choice": exercised,
                "relation_turn": (
                    f"{hero}→{protector}@trust"
                    if (not dirty and i == min(3, total)) else None
                ),
            }
            entries.append(entry)
        return {"entries": entries}

    def _ep_title(self, i: int, act: int) -> str:
        pool = {
            1: ["血还没干", "最后一台手术", "倒计时"],
            2: ["这不是药", "第一次用药", "有人在看着"],
            3: ["他不信", "数字会说话", "多活三天"],
            4: ["赢了，然后呢", "弹劾", "功劳太重"],
        }
        names = pool.get(act, pool[3])
        return names[i % len(names)]

    def _core_goal(self, i: int, act: int) -> str:
        return {
            1: "完成物资储备并切断退路",
            2: "让第一个人相信现代手段有效",
            3: "把一次成功兑换成更深的信任，同时接住它带来的后果",
            4: "在政治清算中存活并改变历史进程",
        }[act]

    # ------------------------------------------------------------------ 事实账本
    def _ledger(self, ctx: dict, dirty: bool, user: str) -> dict:
        total = int(ctx.get("total_episodes", 8))
        cast = ctx.get("cast") or []
        names = {c.slot: c.name for c in cast}
        hero = names.get("A", "周牧")
        rival = names.get("D", "崔敬")
        m3, m4, m5 = min(3, total), min(4, total), min(5, total)

        if dirty:
            # 四个典型账目缺陷，每个对应一条 FC 规则
            return {"entries": [
                {   # FC01：倒计时没有到期集 —— 它不是倒计时，是气氛
                    "id": "F1", "kind": "countdown", "subject": "三日之约",
                    "statement": "主帅给了主角三天时间", "established_at": 1,
                    "due_at": None, "expected_mentions": [min(2, total)],
                },
                {   # FC02：无限容量的资产被登记为"快用完了"
                    "id": "F2", "kind": "resource", "subject": "青霉素余量",
                    "statement": "青霉素快用完了，只剩三支", "established_at": 1,
                    "remaining": 3,
                    "expected_mentions": [min(2, total), m4],
                },
                {   # FC03：知情者与禁知者重叠，且引用了不存在的角色
                    "id": "F3", "kind": "knowledge", "subject": "青霉素的真正来源",
                    "statement": "只有周牧知道药从哪里来", "established_at": 1,
                    "holders": [hero, "沈砚"], "forbidden_holders": [hero, rival],
                    "expected_mentions": [],
                },
                {   # FC04：承接指向了不存在的集号
                    "id": "F4", "kind": "promise", "subject": "给主帅的军令状",
                    "statement": "周牧立下军令状：三天医不好就陪葬", "established_at": m3,
                    "expected_mentions": [99],
                },
            ], "note": "缺陷注入版账本"}

        entries: list[dict] = [
            {
                "id": "F1", "kind": "countdown", "subject": "三日之约",
                "statement": f"主帅给了三天，第 {min(3, total)} 集必须见结果",
                "established_at": 1, "due_at": min(3, total),
                "expected_mentions": [min(2, total), min(3, total)],
            },
            {
                # 资源账目刻意指向一个真正有限的资源（洁净器械），而不是无限容量的金手指本身。
                # 这正是 FC02 想教的写法：无限的东西不能记账，能记账的必然是有限的东西。
                "id": "F2", "kind": "resource", "subject": "洁净器械余量",
                "statement": "随军只有三套洁净器械，用过一次就少一套",
                "established_at": 1, "remaining": 3,
                "expected_mentions": [min(2, total), m4],
            },
            {
                "id": "F3", "kind": "knowledge", "subject": "青霉素的真正来源",
                "statement": f"只有{hero}知道药从哪里来", "established_at": 1,
                "holders": [hero], "forbidden_holders": [rival],
                "expected_mentions": [],
            },
            {
                "id": "F4", "kind": "promise", "subject": "给主帅的军令状",
                "statement": f"{hero}立下军令状：三天医不好就陪葬",
                "established_at": m3, "expected_mentions": [m4],
            },
            {
                "id": "F5", "kind": "possession", "subject": "随军医官的腰牌",
                "statement": f"腰牌在第 {min(3, total)} 集交到{hero}手上",
                "established_at": 1, "expected_mentions": [min(3, total)],
            },
            {
                "id": "F6", "kind": "position", "subject": "主角的身份",
                "statement": "从随军杂役升为代医官", "established_at": 1,
                "expected_mentions": [min(3, total), m5],
            },
        ]
        for i in range(7, total + 1, 2):
            entries.append({
                "id": f"F{i}", "kind": "possession", "subject": f"第 {i} 集的军情文书",
                "statement": f"军情文书第 {i} 集到帐，必须当集处置",
                "established_at": i, "expected_mentions": [i],
            })
        self._ensure_coverage(entries, ctx)
        return {"entries": entries, "note": ""}

    @staticmethod
    def _ensure_coverage(entries: list[dict], ctx: dict) -> None:
        """保证 FC04：第三幕起每一集都至少被一笔账覆盖。

        Mock 里必须显式做这件事，否则"账本覆盖不全"会成为默认状态，
        而干净路径永远过不了账本门 —— 那样就分不清是引擎错了还是夹具错了。
        做法是把未被覆盖的集号摊派给已有账目的承接范围（一笔账可以承接多集）。
        """
        late = [e.get("episode") for e in (ctx.get("outline") or [])
                if e.get("act", 0) >= 3]
        covered = {ep for e in entries for ep in e.get("expected_mentions", [])}
        for ep in late:
            if ep in covered:
                continue
            target = next((e for e in entries if e["kind"] == "promise"), entries[-1])
            target["expected_mentions"] = sorted(set(target["expected_mentions"]) | {ep})
            covered.add(ep)

    # ------------------------------------------------------------------ 散文故事
    def _prose_story(self, ctx: dict, dirty: bool, user: str) -> dict:
        """生成 ProseStory mock。

        6–8 段自然散文，对白嵌入叙述，不出现广播剧标签。
        """
        entry = ctx.get("outline_entry") or {}
        ep_no = int(entry.get("episode", 1))
        cast = ctx.get("cast") or []
        hero = next((c.name for c in cast if c.slot == "A"), "周牧")
        protector = next((c.name for c in cast if c.slot == "C"), "裴肃")
        rival = next((c.name for c in cast if c.faction == "antagonist"), "崔敬")
        witness = next((c.name for c in cast if c.slot == "E"), None)

        title = entry.get("title", f"第{ep_no}章")

        para_templates = [
            (
                f"帐外的雨已经下了整整一夜。{hero}把最后一支针管放进开水里烫了烫，"
                f"手指稳得像在做一台演习了无数次的手术。她对身后的目光毫不在意"
                f"——那些目光里有怀疑，有惧怕，甚至还有几分看热闹的意思。"
                f"「他这条命能不能留，看今晚。」她没有回头，声音比帐外的雨还凉。"
            ),
            (
                f"{protector}站在帐门口，甲片上的雨水还在往下淌。他没有进来，"
                f"只是隔着三步的距离看完了{hero}给伤兵清创的整个过程。"
                f"「这不是军医的手法。」他开口，语调沉得像一块铁。"
                f"「确实不是。」{hero}把手上的血擦在围裙上，「但你需要的是结果。」"
            ),
            (
                f"伤兵的体温在后半夜开始回落。{hero}坐在他身边，每隔一刻钟摸一次他的额头。"
                f"帐外的马蹄声来了又走，走了又来，但她一次都没有抬头。"
                f"伤口周围的红色已经退了将近一半——这是她到这个世界以来见到的第一个好消息。"
            ),
            (
                f"天还没亮，{rival}就带着人进了帐。他扫了一眼伤兵的脸色，"
                f"又扫了一眼{hero}手上那支空了的针管，嘴角往下沉了沉。"
                f"「你给他打了什么？」他问，语气像在审一个犯人。"
                f"{hero}站起来，平视着他的眼睛：「能让他活过今天的东西。」"
            ),
            (
                f"消息传得比风还快。上午还没过完，营里已经有人在传"
                f"——那个来历不明的女人用手里的药救回了一条命。"
                f"有人信，有人不信，也有人开始盘算怎么从她手里把这药弄过来。"
                f"{hero}什么也没解释。她把剩下的药收进贴身的布袋里，"
                f"在袋子外面打了个死结。「下一支，」她在心里说，「是给下一个人的。」"
            ),
            (
                f"午后，{witness or '一个老军医'}找到了{hero}。他没有问药的事，"
                f"只是站在伤兵身边看了很久，久到{hero}以为他要开口责难。"
                f"「四十年了，」老人终于开了口，声音像旧木被碾碎，"
                f"「我没有见过一个人可以在伤口溃烂到这种程度之后还能退烧。」"
                f"他转过脸来，满是皱纹的眼里有一种比惊讶更沉的东西。"
                f"「所以我留下来了。」他说，「我要看到结果。」"
            ),
            (
                f"第三天的黄昏，伤兵睁开了眼睛。他第一个看见的人就是{hero}。"
                f"「别动。」她在他说出任何话之前按住了他的肩膀。"
                f"「你的命是我跟人用头换来的，别再弄丢了。」"
                f"帐帘被风掀开一条缝，夕阳的光落在那支空了第二次的针管上。"
                f"{hero}把那道光看了一眼，然后把它和针管一起收进了布袋。"
            ),
            (
                f"入夜之后，{protector}又来了。这一次他没有站在门外。"
                f"他走进来，在伤兵的床前站了一会儿，然后转身对{hero}说了一句话，"
                f"声音压得极低：「明天，会有人来查你。」他顿了一下，"
                f"甲片在烛火下泛着冷光。「你救回来的每一个人，我都记着。」"
                f"他说完就走了，留下{hero}一个人在灯影里。"
                f"她低头看了看自己的手——那双做了无数次急救的手，"
                f"在这个世界第一次有了分量。"
            ),
        ]

        # 取 6–8 段，按集号循环选择
        count = min(8, max(6, len(para_templates)))
        selected = [para_templates[i % len(para_templates)] for i in range(count)
                    if (i + ep_no) % len(para_templates) < len(para_templates)][:count]
        if len(selected) < 6:
            selected = para_templates[:count]

        paragraphs = [ProseParagraph(text=t) for t in selected]

        return {
            "title": title,
            "paragraphs": [p.model_dump() for p in paragraphs],
        }

    # ------------------------------------------------------------------ 剧本
    # ---- Phase 1.5: Episode Plan (轻量规划) ----
    def _episode_plan(self, ctx: dict, dirty: bool, user: str) -> dict:
        entry = ctx.get("outline_entry") or {}
        ep_no = int(entry.get("episode", 1))
        return {
            "episode": ep_no,
            "title": entry.get("title", f"第{ep_no}集"),
            "goal": entry.get("core_goal", "推进主线"),
            "dramatic_question": "主角能挺过这一关吗？",
            "hook": "开场直接进入危机现场",
            "beats": [
                {"order": 1, "function": "hook", "event": "危机爆发",
                 "new_information": "敌人出现", "character_choice": "迎战而非退缩",
                 "consequence": "受伤但守住阵地", "tension": 7},
                {"order": 2, "function": "conflict", "event": "资源耗尽",
                 "new_information": "隐藏盟友的身份", "character_choice": "信任新盟友",
                 "consequence": "获得关键资源", "tension": 8},
                {"order": 3, "function": "climax", "event": "决战",
                 "new_information": "敌人的真正目的", "character_choice": "牺牲次要目标",
                 "consequence": "击退敌人但留下隐患", "tension": 9},
                {"order": 4, "function": "cliffhanger", "event": "更大的威胁浮现",
                 "new_information": "只是更大阴谋的序幕", "character_choice": "决定追查到底",
                 "consequence": "下一集的方向", "tension": 8},
            ],
            "reversal": "盟友原来是敌人安插的间谍",
            "climax": "主角在绝境中做出抉择",
            "cliffhanger": "幕后黑手露出真容",
            "continuity_notes": "承接前一集的叛国案线索",
        }

    def _episode(self, ctx: dict, dirty: bool, user: str) -> dict:
        entry = ctx.get("outline_entry") or {}
        ep_no = int(entry.get("episode", 1))
        duration = int(ctx.get("duration_sec", 180))
        cast = ctx.get("cast") or []
        slice_ = ctx.get("continuity") or {}
        fakes = bool(ctx.get("_fakes"))
        speaker = next((c.name for c in cast if c.slot == "A"), None) or "周牧"
        rival = next((c.name for c in cast if c.faction == "antagonist"), "崔敬")
        protector = next((c.name for c in cast if c.slot == "C"), "裴肃")
        witness = next((c.name for c in cast if c.slot == "E"), None)

        # tier2 注入：抹掉"上一次成功的副作用"的因果痕迹（结构仍完全合规）
        backlash = "他们分不到好处。" if (ctx.get("_tier2") or fakes) else MARK_CAUSALITY

        # 反转行的强度：声明了反转却把峰值压低 → 假反转（Tier-1 可判定）
        reversal_peak = 5 if fakes else 9
        # 兑换行：fakes 时全部省略，正文于是不兑现任何声明
        three_days = "些时间" if fakes else "三天"

        beats: list[dict] = []

        # ---- S1 黄金钩子：首个声音必须是突发型听觉事件，禁止背景介绍
        beats.append(self._beat("S1", "黄金钩子", 0, 3, [
            self._line(f"e{ep_no}-S1-1", "sfx", None, "门被猛地撞开，木屑炸裂", 0.0, 1.0, "AV1", "hook", 6),
            self._line(f"e{ep_no}-S1-2", "sfx", None, "帐外雨声压下来，混着远处哀号", 1.0, 2.0, "AV4", None, 4),
            self._line(f"e{ep_no}-S1-3", "dialogue", protector, "他不能死。", 2.0, 3.0, None, "hook", 7),
        ]))

        # ---- S2 矛盾四要素：谁 / 在哪 / 什么麻烦 / 想做什么
        beats.append(self._beat("S2", "矛盾四要素", 3, 10, [
            self._line(f"e{ep_no}-S2-1", "sfx", None, "雨点砸在营帐顶上", 3.0, 4.0, "AV4", None, 3),
            self._line(f"e{ep_no}-S2-2", "dialogue", protector, "这是北伐大营。他的伤口在烂。", 4.0, 6.4, None, "beat", 5),
            self._line(f"e{ep_no}-S2-3", "dialogue", speaker, f"我能救。给我{three_days}。", 6.4, 8.0, None, "beat", 6),
            self._line(f"e{ep_no}-S2-4", "dialogue", rival, "你一个随军的,凭什么?", 8.0, 10.0, None, "beat", 6),
        ]))

        # ---- S3 首次释放：必须完成一次压抑→爆发
        s3 = [
            self._line(f"e{ep_no}-S3-1", "sfx", None, "药碾滚过桌面", 10.0, 10.8, "AV4", None, 3),
            self._line(f"e{ep_no}-S3-2", "dialogue", rival, "他要是死了，你赔命?", 10.8, 13.0, None, "beat", 7),
            self._line(f"e{ep_no}-S3-3", "dialogue", speaker, f"{three_days}。他要是死了，我陪葬。", 13.0, 16.0, None, "release", 9),
            self._line(f"e{ep_no}-S3-4", "sfx", None, "帐内所有人同时安静下来", 16.0, 17.0, "AV1", "release", 8),
            self._line(f"e{ep_no}-S3-5", "dialogue", protector, "照他说的做。", 17.0, 19.0, None, "release", 8),
        ]
        if dirty:
            s3.append(self._line(f"e{ep_no}-S3-6", "narration", "旁白",
                                 DEFAULT_VISUAL_LEAK, 19.0, 20.0, None, None, 4))
        beats.append(self._beat("S3", "第一次释放", 10, 30, s3))

        # ---- S4 中段推进：台词短、碎、锋利
        s4 = [
            self._line(f"e{ep_no}-S4-1", "sfx", None, "帐帘掀起，风灌进来", 30.0, 31.0, "AV4", None, 4),
            self._line(f"e{ep_no}-S4-2", "dialogue", speaker, "把水烧开。所有人洗手。", 31.0, 34.0, None, "beat", 5),
            self._line(f"e{ep_no}-S4-3", "dialogue", rival, "他在折腾什么?", 34.0, 36.0, None, "beat", 5),
            self._line(f"e{ep_no}-S4-4", "dialogue", speaker,
                       DEFAULT_LONG_LINE if dirty else "把水烧开。洗手。", 36.0, 42.0, None, "beat", 5),
            self._line(f"e{ep_no}-S4-5", "sfx", None, "水沸声与木桶碰撞", 39.0, 40.0, "AV4", None, 3),
        ]
        if witness:
            # K14：降维见证者必须真的出场 —— 他的折服过程就是听众建立信任的过程
            s4.append(self._line(f"e{ep_no}-S4-5b", "dialogue", witness,
                                 "老夫行医四十年，没见过这样治的。", 45.0, 49.0, None, "beat", 6))
        s4.append(self._line(f"e{ep_no}-S4-6", "dialogue", protector,
                             # 可量化成果的兑现行：数字必须真的出现在正文里
                             "还没停。" if fakes else f"第 {ep_no * 7} 个。体温退了。",
                             55.0, 58.0, None, "beat", 7))
        s4 += [
            self._line(f"e{ep_no}-S4-7", "dialogue", speaker, "还没完。别停。", 58.0, 60.0, None, "beat", 6),
            self._line(f"e{ep_no}-S4-8", "sfx", None, "伤兵从床板上翻下身，重重跪地", 80.0, 81.0, "AV1", "reversal", reversal_peak),
            self._line(f"e{ep_no}-S4-9", "dialogue", protector, "他站起来了。他真的站起来了。", 81.0, 85.0, None, "reversal", reversal_peak),
            self._line(f"e{ep_no}-S4-10", "dialogue", rival, "这个人的来历，必须查。", 105.0, 108.0, None, "beat", 7),
            self._line(f"e{ep_no}-S4-11", "sfx", None, "帐外脚步声由远及近", 108.0, 109.0, "AV4", None, 5),
        ]
        beats.append(self._beat("S4", "中段推进", 30, 120, s4))

        # ---- S5 二次反转 + 声明兑现行 ----
        s5 = [
            self._line(f"e{ep_no}-S5-0", "sfx", None, "帐外雨声持续压着", 120.0, 121.0, "AV4", None, 5),
            self._line(f"e{ep_no}-S5-1", "sfx", None, "一叠奏章被摔在案上", 121.0, 122.0, "AV1", "reversal", 8),
            self._line(f"e{ep_no}-S5-2", "dialogue", rival, "他用的不是医术。是妖术。", 121.0, 125.0, None, "reversal", 8),
            self._line(f"e{ep_no}-S5-3", "dialogue", protector, "谁的折子?", 125.0, 127.0, None, "beat", 7),
            self._line(f"e{ep_no}-S5-4", "dialogue", rival, f"十七位。{backlash}", 127.0, 131.0, None, "reversal", 9),
            self._line(f"e{ep_no}-S5-5", "sfx", None, "帐外雨声突然停了", 131.0, 132.0, "AV3", "reversal", 9),
            self._line(f"e{ep_no}-S5-6", "dialogue", protector, "把折子收起来。", 148.0, 151.0, None, "beat", 7),
            self._line(f"e{ep_no}-S5-7", "dialogue", rival, "十七位同僚的名字，我记着。", 151.0, 155.0, None, "beat", 7),
        ]
        s5 = self._insert_deliveries(s5, ep_no, entry, slice_, fakes, dirty,
                                     speaker, protector, rival, witness, cast)
        beats.append(self._beat("S5", "二次反转", 120, 170, s5))

        # ---- S6 强钩子：强度必须大于本集所有冲突
        beats.append(self._beat("S6", "强钩子", 170, 180, [
            self._line(f"e{ep_no}-S6-1", "sfx", None, "纸张被缓缓折起", 170.0, 171.0, "AV1", None, 6),
            self._line(f"e{ep_no}-S6-2", "dialogue", protector, "把药给他。", 171.0, 173.0, None, "cliffhanger", 9),
            self._line(f"e{ep_no}-S6-3", "dialogue", speaker, "为什么?", 173.0, 174.0, None, "cliffhanger", 9),
            self._line(f"e{ep_no}-S6-4", "dialogue", protector, "因为他今晚要杀的人，是你。", 174.0, 180.0, None, "cliffhanger", 10),
        ]))

        if duration != 180:
            beats = self._rescale(beats, duration)

        return {
            "episode": ep_no,
            "title": entry.get("title", f"第 {ep_no} 集"),
            "duration_sec": duration,
            "beats": beats,
            "cliffhanger_hook_type": "H6",
            # R06：clean 路径恰好 1 条线索；dirty 时写成 2 条，触发"单集单目标"告警
            "threads": (["主角用现代医疗手段换取生存空间"]
                        if not dirty else
                        ["主角用医疗手段换生存", "朝堂对主角的猜忌"]),
            "revision": 0,
        }

    def _insert_deliveries(self, s5: list[dict], ep_no: int, entry: dict,
                           slice_: dict, fakes: bool, dirty: bool,
                           speaker: str, protector: str, rival: str,
                           witness: str | None, cast: list | None = None) -> list[dict]:
        """插入"声明兑现行"。

        这一段是 Mock 最关键的部分：inject_fakes 关掉时，正文必须真的兑现大纲声明过的
        每一件事，否则干净路径会被自己的证据层判为标签空转。
        开起来时全部省略 —— 于是"声明与正文脱节"被真实地造出来，供证据层捕获。
        """
        if dirty or fakes:
            rows: list[tuple[str, str, str]] = []
            if fakes and entry.get("act", 0) >= 3:
                # 唯一保留的一行：把"反转无新信息"造出来（峰值已由 reversal_peak 压低）
                rows.append(("reversal", rival, "还是这些人，还是这些话。"))
            # 认知越界：禁知者说出了不该知道的事
            if fakes:
                rows.append(("leak", rival, LEAK_LINE))
            # 资源漂移：账目说还有余量，正文偏说用完了。
            # 资源账目在切片的 active 桶里。不造这一行，resource_drift 就是一个
            # 永远无人触发的词表条目 —— demo 声称"全覆盖"，实际只覆盖 6/7。
            if fakes:
                for fact in ((slice_.get("facts") or {}).get("active") or []):
                    if fact.get("kind") == "resource" and (fact.get("remaining") or 0) > 0:
                        subject = str(fact.get("subject", "")).replace("余量", "")
                        rows.append(("drift", speaker, f"{subject}全用完了。"))
                        break
        else:
            rows = []
            if entry.get("exercised_choice"):
                # 选择必须由**做出这个选择的那个角色**说出来。用主角顶替所有角色的选择，
                # 会让"每个有声角色都要有自己的选择"这条要求在正文层面落空 ——
                # 校验能过（标记在），但听众听到的仍然只有主角一个人在选。
                actor = self._choice_actor(entry.get("exercised_choice"), cast or [], speaker)
                rows.append(("choice", actor, MARK_CHOICE))
            if entry.get("relation_turn"):
                rows.append(("relation", protector, MARK_RELATION))
            if entry.get("deployment"):
                asset = self._deployed_asset(entry)
                rows.append(("deploy", protector,
                             f"那些{asset}，再给他留一份。" if asset else "那些东西，再给他留一份。"))
            if entry.get("gadget_failed"):
                rows.append(("fail", speaker, "药不够了。这次得靠他自己。"))
            if entry.get("act", 0) >= 3:
                rows.append(("reversal", protector, MARK_NEW_INFO))
            facts = (slice_.get("facts") or {})
            for fact in (facts.get("due") or []):
                rows.append(("fact", speaker, f"{fact['subject']}到了。"))
            for fact in (facts.get("must_mention") or []):
                if any(fact["id"] == d["id"] for d in (facts.get("due") or [])):
                    continue
                rows.append(("fact", protector, f"{fact['subject']}，我记着。"))
            # 关系收束：尾段集数必须让听众听见这条关系现在处于什么状态。
            # 用 evidence_hint 原文作为音效落点 —— 它是"该关系的听觉签名"，
            # 也正是 RL02 在收束区检索的痕迹。
            for rel in ((slice_.get("relations") or {}).get("landings") or []):
                hint = (rel.get("landing_hint") or "").strip()
                if hint:
                    rows.append(("landing", None, hint))

        if not rows:
            return s5
        cursor, step = 133.0, 3.4
        out = list(s5)
        for idx, (kind, who, text) in enumerate(rows):
            start = cursor + idx * step
            if kind == "landing":
                # 关系收束落点写成音效，而不是台词：证据提示本身描述的就是
                # "这一段戏听起来是什么样"，把它当台词念会变成旁白式的解释。
                out.append(self._line(f"e{ep_no}-S5-D{idx}", "sfx", None, text,
                                      round(start, 2), round(start + 2.6, 2),
                                      "AV4", None, 6))
                continue
            event = "reversal" if kind == "reversal" else "beat"
            peak = 8 if kind == "reversal" else 6
            out.append(self._line(f"e{ep_no}-S5-D{idx}", "dialogue", who, text,
                                  round(start, 2), round(start + 2.6, 2),
                                  None, event, peak))
        return out

    @staticmethod
    def _choice_actor(exercised: str, cast: list, fallback: str) -> str:
        """从 exercised_choice 串里解出"做出这个选择的人"，解析不出才退回主角。"""
        name = (exercised or "").split("@")[0].strip()
        if not name:
            return fallback
        voiced = {c.name for c in cast if getattr(c, "is_voiced", False)}
        return name if name in voiced else fallback

    @staticmethod
    def _deployed_asset(entry: dict) -> str:
        deployment = entry.get("deployment") or ""
        if "：" in deployment:
            tail = deployment.split("：", 1)[1]
            return tail.split("用于", 1)[0].strip()
        return ""

    # ------------------------------------------------------------------ 语义裁判
    def _judge(self, ctx: dict, dirty: bool, user: str) -> dict:
        """可运行的代理裁判。

        生产环境应替换为真实模型；但这里不是硬编码的通过/失败 ——
        判据本身是可执行的（在给定上下文里检索证据），目的是让语义校验这条分支真实地走通。

        声明裁决的判据与真实裁判同构：**先在正文里找证据，找不到就判未兑现**，
        并按伪证模式分类。区别只在于"找证据"的方式 —— 真实模型靠语义理解，
        Mock 靠可检索的标记与结构化字段。
        """
        items: list[dict] = ctx.get("judge_items") or []
        context: dict = ctx.get("context") or {}
        text: str = context.get("episode_text") or ""
        verdicts: list[dict] = []

        for it in items:
            if it.get("kind") == "claim":
                verdicts.append(self._judge_claim(it, text, context))
                continue

            rule_id = it.get("rule_id", "")
            payload = it.get("payload") or {}
            entry = payload.get("outline_entry") or {}
            gadget = payload.get("gadget") or {}
            ep_no = int(entry.get("episode", 1))
            passed, reason = True, ""

            if rule_id == "K06":
                if entry.get("loop_step") == "S5" and not entry.get("side_effect_of"):
                    passed = False
                    reason = (f"第 {ep_no} 集标记为降维循环第 5 步，但核心难题未标注源于哪一次成功，"
                              f"退化为事件流水账")
                elif entry.get("side_effect_of") and int(entry["side_effect_of"]) >= ep_no:
                    passed = False
                    reason = f"第 {ep_no} 集引用的『前一次成功』（第 {entry['side_effect_of']} 集）不早于本集"

            elif rule_id == "K16":
                if (gadget.get("core_conflict") or "").strip() and \
                        not (gadget.get("escalated_conflict") or "").strip():
                    passed = False
                    reason = "金手指覆盖了核心冲突，但未上移冲突层级，主线张力会被抹平"

            elif rule_id == "K08":
                if ep_no <= 2 and "release" not in json.dumps(payload.get("episode") or {},
                                                             ensure_ascii=False):
                    passed = False
                    reason = "首次降维未走完『被怀疑 → 验证 → 震动』三步"

            elif rule_id == "R13":
                blob = json.dumps(payload, ensure_ascii=False)
                for bad in ("强制爱", "擦边", "下药", "绑架"):
                    if bad in blob:
                        passed = False
                        reason = f"命中合规风险词：{bad}"
                        break

            verdicts.append({"rule_id": rule_id, "passed": passed,
                             "reason": reason, "evidence": reason[:60]})

        unclaimed: list[str] = []
        if ctx.get("_fakes"):
            # 模拟"正文里出现了账本未登记的新事实"：这一条必须走到 EV05，
            # 否则未登记伏笔的提取链路从未被验证过。
            unclaimed = ["随军参将沈砚（正文首次出现，账本未登记）"]
        return {"verdicts": verdicts, "unclaimed": unclaimed}

    def _judge_claim(self, item: dict, text: str, context: dict) -> dict:
        claim_id = item.get("claim_id", "")
        rule_id = item.get("rule_id", "")
        kind = claim_id.split(".", 1)[-1]
        payload = item.get("payload") or {}
        passed, pattern, evidence, reason = True, "none", "", ""

        def fail(pat: str, why: str, ev: str = "") -> dict:
            return {"claim_id": claim_id, "rule_id": rule_id, "passed": False,
                    "reason": why, "evidence": ev or "（正文中找不到对应载体）",
                    "fake_pattern": pat}

        if kind.startswith("side_effect_of"):
            if MARK_CAUSALITY in text:
                evidence = MARK_CAUSALITY
            else:
                return fail("fake_causality", "声明本集难题源于前一次成功，但正文中无人指认那次成功")

        elif kind.startswith("exercised_choice"):
            if MARK_CHOICE in text:
                evidence = MARK_CHOICE
            else:
                return fail("label_only", "大纲声明了本集演练的角色选择，正文中找不到被执行的动作")

        elif kind.startswith("relation_turn"):
            if MARK_RELATION in text:
                evidence = MARK_RELATION
            else:
                return fail("fake_trust", "声明本集发生关系转折，但正文中没有可指认改变相处方式的事件")

        elif kind.startswith("reversal"):
            if MARK_NEW_INFO in text:
                evidence = MARK_NEW_INFO
            else:
                # 走到这里说明峰值静态检查已通过（否则会被 Tier-1 拦下），
                # 剩下唯一可能是"反转没有引入新信息"
                return fail("fake_reversal", "反转发生但没有引入任何新信息 —— 只是把已知情况又说了一遍")

        elif kind.startswith("quantified_gain"):
            if _DIGIT_RE.search(text):
                evidence = next((ln for ln in text.splitlines() if _DIGIT_RE.search(ln)), "")
            else:
                return fail("label_only", "声明了可量化成果，正文中没有任何数量")

        elif kind.startswith("deployment"):
            asset = self._deployed_asset({"deployment": payload.get("deployment", "")})
            if asset and (asset in text or (len(asset) >= 2 and asset[:2] in text)):
                evidence = f"正文中出现资产指认：{asset}"
            elif any(p in text for p in ("那些东西", "那东西", "吃食")):
                # 明确的指代也算兑现 —— 这一条很重要：要求逐字出现资产名会制造假阳性，
                # "他给伤兵发那东西"在广播剧里是完全合法的表达。
                evidence = "正文中以指代方式提到该资产"
            else:
                return fail("label_only", "声明了定点投放，但正文中找不到该资产的任何指认")

        elif kind.startswith("gadget_failed"):
            markers = ("不够", "没用", "来不及", "失手", "失败", "救不回来", "缺")
            hit = next((m for m in markers if m in text), None)
            if hit:
                evidence = hit
            else:
                return fail("label_only", "声明金手指失效，但正文中没有任何失败语义")

        elif kind.startswith("knowledge"):
            leaked = payload.get("forbidden_holders") or []
            for holder in leaked:
                for ln in text.splitlines():
                    if ln.startswith(f"{holder}:") and any(
                        t in ln for t in self._subject_tokens(payload.get("subject", ""))
                    ):
                        return fail("knowledge_leak", f"{holder} 说出了他不该知道的「{payload.get('subject')}」", ln)
            evidence = "禁知者本集未提及该信息"

        elif kind.startswith(("countdown", "carried_fact", "resource")):
            subject = payload.get("subject", "")
            tokens = self._subject_tokens(subject)
            hit = next((ln for ln in text.splitlines() if any(t in ln for t in tokens)), None)
            if hit:
                # 静态层漏网的资源矛盾在这里兜底：与 evidence._resource_drift 同构，
                # 判定词表同源（context.depletion_markers ← 规则库 resource_balance）。
                remaining = payload.get("remaining")
                markers = context.get("depletion_markers") or []
                if (remaining is not None and remaining > 0 and markers
                        and any(m in hit for m in markers)):
                    return fail("resource_drift",
                                f"账目声明「{subject}」余量 {remaining}，"
                                f"但正文写道『{hit.strip()}』", hit)
                evidence = hit
            elif kind.startswith("resource"):
                passed, evidence = True, "本集未使用该资源，账目不被违背"
            elif kind.startswith("countdown"):
                return fail("countdown_drift", f"账目「{subject}」本集到期，但正文中从未触及它")
            else:
                return fail("label_only", f"本集声明承接账目「{subject}」，但正文中从未提及")

        return {"claim_id": claim_id, "rule_id": rule_id, "passed": passed,
                "reason": reason, "evidence": evidence, "fake_pattern": pattern}

    @staticmethod
    def _subject_tokens(subject: str) -> list[str]:
        """与 validators.evidence.subject_tokens 同构的简化版，避免 Mock 反向依赖校验层。"""
        if len(subject) < 2:
            return [subject] if subject else []
        return [subject[i:i + 2] for i in range(len(subject) - 1)]

    # ------------------------------------------------------------------ 定向改写
    def _line_rewrites(self, ctx: dict, dirty: bool, user: str) -> dict:
        """只处理被点名的行。

        这是"精准修复优于整段重写"的具体形态：31 字的长句拆成短句，
        含"嘴角"的视觉描写替换为听觉事件，其余行原样回传。
        """
        targets: list[dict] = ctx.get("target_lines") or []
        out: list[dict] = []
        for raw in targets:
            ln = dict(raw)
            text = ln.get("text", "")
            for word in ctx.get("blacklist") or []:
                if word in text:
                    head = text.split(word)[0].rstrip("，。！？、 ")
                    text = f"{head}（话说到一半被打断）" if head else "（一声脆响，话语被打断）"
                    ln["kind"] = "sfx"
                    ln["speaker"] = None
                    ln["sfx_category"] = "AV2"  # 被中断的台词
                    break
            if len("".join(c for c in text if not c.isspace())) > 20:
                for sep in ("。", "！", "？", "，"):
                    idx = text.find(sep)
                    if 0 < idx < 20:
                        text = text[: idx + 1]
                        break
                else:
                    text = text[:20]
            ln["text"] = text
            out.append(ln)
        return {"lines": out, "note": f"定向改写 {len(out)} 行"}

    # ------------------------------------------------------------------ 工具
    @staticmethod
    def _line(lid, kind, speaker, text, start, end, sfx_cat, event, peak) -> dict:
        return {"id": lid, "kind": kind, "speaker": speaker, "text": text,
                "start_sec": start, "end_sec": end, "sfx_category": sfx_cat,
                "event": event, "emotion_peak": peak, "measured_sec": None}

    @staticmethod
    def _beat(segment, label, start, end, lines) -> dict:
        return {"segment": segment, "label": label, "start_sec": start,
                "end_sec": end, "lines": lines}

    @staticmethod
    def _rescale(beats: list[dict], duration: int) -> list[dict]:
        """按目标时长线性缩放时间轴。真实实现应由节拍表解算 + TTS 实测时长回填。"""
        factor = duration / 180.0
        out = []
        for beat in beats:
            out.append({
                **beat,
                "start_sec": round(beat["start_sec"] * factor, 2),
                "end_sec": round(beat["end_sec"] * factor, 2),
                "lines": [{**ln,
                           "start_sec": round(ln["start_sec"] * factor, 2),
                           "end_sec": round(ln["end_sec"] * factor, 2)}
                          for ln in beat["lines"]],
            })
        if factor > 1.0:
            out = MockLLMProvider._fill_node_gaps(out, duration)
        return out

    @staticmethod
    def _fill_node_gaps(beats: list[dict], duration: int,
                        max_gap: float = 30.0) -> list[dict]:
        """拉长时长后，把超过 max_gap 的情绪节点空档补上（R04）。

        这一段只服务于 Mock 的固定模板：模板按 180s 编排，线性拉到 300s 后
        相邻情绪节点的间隔会超过 30 秒。真实实现里这件事由模型按节拍表重新编排 ——
        这里补是因为"换一个 --duration 就报一串假错"会让使用者误判引擎有问题。
        补进去的是音效节点（R11 豁免短节拍，且音效不受台词字数限制）。
        """
        marks = sorted(
            ln["start_sec"] for b in beats for ln in b["lines"] if ln.get("event")
        )
        if not marks:
            return beats
        bounds = [0.0] + marks + [float(duration)]
        gaps = [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)
                if bounds[i + 1] - bounds[i] > max_gap]

        for idx, (lo, hi) in enumerate(gaps):
            # 均匀切开，而不是只补一个中点 —— 空档可能有 2 倍于阈值的宽度
            n = int((hi - lo) // max_gap)
            for k in range(1, n + 1):
                t = round(lo + (hi - lo) * k / (n + 1), 2)
                target = next(
                    (b for b in beats if b["start_sec"] <= t <= b["end_sec"]),
                    beats[-1],
                )
                target["lines"].append(MockLLMProvider._line(
                    f"{target['segment']}-N{idx}{k}", "sfx", None,
                    "帐外风声一转，远处传来第二波马蹄", t, round(t + 1.2, 2),
                    "AV4", "beat", 6,
                ))
        for b in beats:
            b["lines"].sort(key=lambda ln: ln["start_sec"])
        return beats
