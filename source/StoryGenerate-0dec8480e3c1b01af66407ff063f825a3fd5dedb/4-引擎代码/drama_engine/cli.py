"""命令行入口。

    # 正常跑（带修复回路）
    python -m drama_engine.cli --idea "我携带无限青霉素和肯德基，辅佐丞相北伐" \
        --assets 青霉素,肯德基 --episodes 8

    # 负向对照一：注入结构缺陷并关闭修复，观察 Tier-1 抓到什么
    python -m drama_engine.cli ... --violations-demo

    # 负向对照二：只注入语义缺陷（结构全绿），观察 Tier-2 抓到什么
    python -m drama_engine.cli ... --tier2-demo

    # 负向对照三：只造"声明与正文脱节"（结构全绿、账本合规），
    # 观察证据评审层如何识别假反转 / 假因果 / 假信任 / 标签空转
    python -m drama_engine.cli ... --evidence-demo
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import RuleLibrary
from .contracts import InMemoryTelemetry, Runtime
from .graph import build_graph
from .modes import get_mode, ModeContext
from .mocks import MockMarketRetriever, MockTTSRenderer


def _resolve_mode(key: str | None) -> ModeContext:
    """CLI helper: resolve mode key → ModeContext."""
    return ModeContext.from_mode(get_mode(key))


def _build_runtime(kind: str, telemetry, inject_violations: bool,
                   inject_tier2: bool, inject_fakes: bool) -> Runtime:
    if kind == "env":
        from .llm.openai_compat import build_provider_from_env

        provider = build_provider_from_env()
    else:
        from .llm.mock import MockLLMProvider

        provider = MockLLMProvider(inject_violations=inject_violations,
                                   inject_tier2=inject_tier2,
                                   inject_fakes=inject_fakes)
    return Runtime(llm=provider, tts=MockTTSRenderer(),
                   market=MockMarketRetriever(), telemetry=telemetry)


def _disable_repair() -> None:
    """关闭修复回路。

    注意要同时改两个模块里的绑定：project.py 用 from-import 拿到了 MAX_ATTEMPTS 的副本，
    只改 _common 是不生效的 —— 这是 Python 里很常见的静默失效。
    """
    from .nodes import _common, episode as ep_mod, project as proj_mod

    _common.MAX_ATTEMPTS = 0
    proj_mod.MAX_ATTEMPTS = 0
    ep_mod.EPISODE_MAX_ATTEMPTS = 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="drama_engine", description="AI 广播剧生成引擎")
    parser.add_argument("--idea", required=True, help="一句话创意")
    parser.add_argument("--episodes", type=int, default=8)
    parser.add_argument("--duration", type=int, default=180, choices=[90, 120, 150, 180, 300])
    parser.add_argument("--assets", default="", help="指定金手指，逗号分隔")
    parser.add_argument("--workspace", default=None, help="规则库所在目录")
    parser.add_argument("--out", default="run-output.json")
    parser.add_argument("--provider", default="mock", choices=["mock", "env"])
    parser.add_argument("--violations-demo", action="store_true",
                        help="注入结构缺陷 + 关闭修复回路，验证 Tier-1 覆盖")
    parser.add_argument("--tier2-demo", action="store_true",
                        help="只注入语义缺陷（Tier-1 全绿），验证语义裁判真的在工作")
    parser.add_argument("--evidence-demo", action="store_true",
                        help="只造声明与正文脱节，验证证据评审层（假反转/假因果/假信任/标签空转）")
    parser.add_argument("--story-mode", default=None, help="故事模式，默认 viral_drama")
    args = parser.parse_args(argv)

    demo = args.violations_demo or args.tier2_demo or args.evidence_demo
    if demo:
        _disable_repair()

    lib = RuleLibrary.load(args.workspace)
    telemetry = InMemoryTelemetry()
    runtime = _build_runtime(
        args.provider, telemetry,
        inject_violations=not (args.tier2_demo or args.evidence_demo),
        inject_tier2=args.tier2_demo,
        inject_fakes=args.evidence_demo,
    )

    app = build_graph(use_checkpoint=not demo)
    mode = _resolve_mode(args.story_mode)
    initial = {
        "brief": {
            "raw_idea": args.idea,
            "target_episodes": args.episodes,
            "target_duration_sec": args.duration,
            "locked_assets": [a.strip() for a in args.assets.split(",") if a.strip()],
        },
        "workspace": str(Path(args.workspace or ".").resolve()),
        "mode_context": mode.model_dump(),
        "episodes": [], "findings": [], "unclaimed": [], "trace": [], "budgets": {},
    }
    config = {
        "configurable": {"lib": lib, "runtime": runtime, "thread_id": "cli"},
        "recursion_limit": 200,
    }
    final = app.invoke(initial, config)

    report = final.get("final_report") or {}
    out_path = Path(args.out)
    # 父目录不存在时先建出来。这一行是"别把已经跑完的一整轮生成结果丢掉"的兜底 ——
    # 报告写不出去而这个错误发生在流程最末端，重跑一次的代价是全部模型调用。
    if out_path.parent and not out_path.parent.exists():
        out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    _print_summary(report)

    if demo:
        _print_findings(report)
    print(f"\n完整产物已写入：{out_path.resolve()}")
    return 0


def _print_summary(report: dict) -> None:
    if not report:
        print("未产出报告 —— 流水线可能被中断。")
        return
    assets = [f"{a['name']}({a['layer']}/{a['curve']})"
              for a in (report.get("gadget") or {}).get("assets", [])]
    behavior = report.get("behavior") or {}
    print("=" * 78)
    print(f"赛道 {report['genre_id']} / 配方 {report['recipe_id']}｜金手指 {assets}")
    print(f"集数 {report['episode_count']}"
          f"｜规则库 formula={report['rule_library']['formula_version']}"
          f" timetravel={report['rule_library']['timetravel_version']}"
          f" continuity={report['rule_library'].get('continuity_version')}"
          f"｜有声角色上限 {report['rule_library']['max_voiced_characters']}")
    if behavior:
        print(f"人物：行为卡 {len(behavior.get('cards', []))} 张"
              f"｜关系边 {len(behavior.get('relations', []))} 条"
              f"｜★违背自身利益的选择 {behavior.get('against_self_interest_total', 0)} 条"
              f"｜主角错误信念「{behavior.get('hero_misbelief') or '（缺）'}」")
    ledger = report.get("ledger") or {}
    cont = report.get("continuity") or {}
    if ledger:
        print(f"账本：{len(ledger.get('entries', []))} 条账目"
              f"｜未登记伏笔 {len(cont.get('unclaimed_facts') or [])} 条"
              f"｜演练角色选择的集数 {list((cont.get('exercised_choices') or {}).keys())}")
    out = report["validation"]["outstanding"]
    hist = report["validation"]["history"]
    print("-" * 78)
    print(f"交付质量（最终残留）：error={out['errors']} warning={out['warnings']}"
          f"｜Tier1={out['tier1']} Tier2={out['tier2']}")
    print(f"过程日志（含已修复）：违规 {hist['total']} 条"
          f"｜Tier1={hist['tier1']} Tier2={hist['tier2']}")
    if out["by_rule"]:
        print("残留规则分布：" + "，".join(f"{k}×{n}"
              for k, n in sorted(out["by_rule"].items(), key=lambda kv: -kv[1])))
    patterns = report["validation"].get("fake_patterns") or {}
    if patterns:
        print("伪证模式分布：" + "，".join(
            f"{k}×{v['count']}{v.get('locations') or ''}" for k, v in patterns.items()))
    print(f"模型调用：{report['budgets']}｜重试轮次：{report['attempts']}")
    print("=" * 78)
    for ep in report["script"][:2]:
        print(f"\n第 {ep['episode']} 集《{ep['title']}》 {ep['duration_sec']}s"
              f"｜钩子 {ep['hook_type']}｜{ep['line_count']} 行｜rev{ep['revision']}")
        for ln in ep["lines"][:9]:
            who = ln["speaker"] or "—  "
            print(f"  [{ln['segment']}] {ln['start_sec']:>6.1f}s {ln['kind']:<9} "
                  f"{who:<5} {ln['text']}")
    if len(report["script"]) > 2:
        print("\n（其余集数见输出文件）")


def _print_findings(report: dict) -> None:
    details = report["validation"]["outstanding"].get("details") or []
    if not details:
        print("\n最终产物无残留违规。")
        return
    print("\n" + "-" * 78)
    print(f"最终残留违规明细（共 {len(details)} 条）")
    print("-" * 78)
    for f in details:
        tier = f"T{f['tier']}"
        pat = f"[{f['pattern']}]" if f.get("pattern") else ""
        print(f"[{f['rule_id']}][{tier}][{f['severity']}]{pat} {f['message'][:96]}")
        if f.get("evidence"):
            print(f"    证据（正文原文）：{f['evidence'][:88]}")
        if f.get("repair_hint"):
            print(f"    → 修复方向：{f['repair_hint'][:88]}")


if __name__ == "__main__":
    sys.exit(main())
