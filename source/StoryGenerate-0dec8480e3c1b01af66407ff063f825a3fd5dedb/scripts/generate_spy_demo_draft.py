"""Generate editorial spy-drama drafts with a real configured model, never Mock.

These are pre-approval writing drafts, not StoryDelivery or READY evidence.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from drama_engine.llm.openai_compat import build_provider_from_env
from drama_engine.llm.router import resolve_spec


class DraftLine(BaseModel):
    speaker: Literal["沈砚", "苏棠", "老秦", "广播声", "音效"]
    text: str
    performance: str = ""


class DraftScene(BaseModel):
    title: str
    dramatic_purpose: str
    lines: list[DraftLine] = Field(min_length=1)


class SpyDemoDraft(BaseModel):
    title: str
    preview_blurb: str
    scenes: list[DraftScene] = Field(min_length=5, max_length=5)
    ending_state: str
    total_spoken_characters: int


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=["complete", "serialized"])
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    provider = build_provider_from_env()
    spec = resolve_spec(
        "episode_writer", temperature=0.65, max_tokens=9000,
        extra={"node_timeout_sec": 240},
    )
    common = (
        "写一份中文广播剧的五分钟可表演剧本草稿。架空旧时代的海港城，谍战背景，"
        "悬疑且多次因果反转，情报争夺仅为戏剧背景，不教授现实可操作技术。"
        "角色只有译电员沈砚、修机师苏棠、站长老秦；广播声只能是三人之一的录音，"
        "不要新增有台词角色。五场，每场约一分钟。台词约 880–960 个汉字，"
        "另外用少量音效和停顿填满五分钟。对白要口语、可演，不能靠旁白解释。"
        "preview_blurb 是播放前两句话、无剧透，40–80 字。"
        "每条台词给一句简短表演提示。"
    )
    if args.kind == "complete":
        brief = (
            "完整版本：主角发现例行天气播报中的一句话与已取消的秘密接头有关。"
            "他误判同伴，后来复核一处声音线索才识破真正泄密者。"
            "五分钟内完成主要任务，揭露卧底并让无辜者脱险，结局闭合。"
        )
    else:
        brief = (
            "长篇版本的前五分钟：主角收到一位已失踪七年的联络人留下的当日录音，"
            "牵出港城多年未解的身份谜团。片段内完成一个局部目标，"
            "结尾出现公平铺垫过的重大反转和明确下一步行动；绝不提前解开主谜。"
        )
    draft = provider.complete_structured(
        spec,
        "你是优秀的中文广播剧编剧。请输出有悬疑张力、清楚声场、完整人物行动的结构化剧本。",
        common + brief,
        SpyDemoDraft,
    )
    spoken = sum(
        len(line.text) for scene in draft.scenes for line in scene.lines
        if line.speaker != "音效"
    )
    evidence = {
        "kind": args.kind,
        "git_sha": subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip(),
        "model_calls": provider.call_history,
        "spoken_characters_observed": spoken,
        "draft": draft.model_dump(mode="json"),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "kind": args.kind, "title": draft.title,
        "spoken_characters": spoken, "model_calls": provider.call_history,
        "output": str(args.output),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
