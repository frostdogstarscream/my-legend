"""回归门：一条命令跑完整验证矩阵，任何断言失败都返回非零退出码。

    python verify_matrix.py            # 全矩阵（干净组合 + 三条对照）
    python verify_matrix.py --quick    # 只跑三条对照与一组干净组合

为什么需要它：多命题扫描曾经抓出单命题验证完全看不见的问题
（K02 只在 8 集以上暴露、K09 只在小集数暴露、R04 只在 300 秒暴露）。
单次通过不叫通过，覆盖了参数空间才算。此后每次改动规则库或校验层，
先跑这个脚本再交付 —— 它就是本引擎的"测试 suite"。

断言依据（与 README 的验证矩阵一致）：
  - 干净路径：交付质量 error=0（warning 允许残留，S02 是刻意保留的
    "低音频适配度必须可见"的设计信号，不许为了零 warning 而静默改掉）。
  - --violations-demo：Tier-1 命中 K / R / CH / RL / FC 五族。
  - --tier2-demo：命中 K06（fake_causality），且无 error 级 Tier-1。
  - --evidence-demo：命中全部 7 种伪证模式。这是最重要的一条 ——
    它复现"规则全过、戏不好看"，且每条命中都必须带正文原文。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

_LEADING_ALPHA = re.compile(r"^[A-Z]+")

from drama_engine.cli import _build_runtime, _disable_repair  # noqa: E402
from drama_engine.config import RuleLibrary  # noqa: E402
from drama_engine.contracts import InMemoryTelemetry  # noqa: E402
from drama_engine.graph import build_graph  # noqa: E402

# 全部 7 种伪证模式。词表里多一种而这里不知道，等于宣告失效 ——
# resource_drift 曾长期处于这种状态：schema 有它、裁判词表有它，
# 但没有任何注入能命中它，直到对照实验逐项核对才暴露。
ALL_FAKE_PATTERNS = {
    "label_only", "fake_reversal", "fake_trust", "fake_causality",
    "countdown_drift", "knowledge_leak", "resource_drift",
}

IDEAS = [
    ("家庭冲突", "我携带无限青霉素和肯德基，辅佐丞相北伐"),
    ("穿越搞事业", "社畜穿越成将军府嫡女，用现代记账法整顿府库"),
]

CLEAN_MATRIX = [  # (集数, 时长) —— 覆盖小集数 / 标准集数 / 长时长三个曾出问题的方向
    (4, 90), (4, 300), (6, 180), (8, 180), (10, 90), (10, 300),
]


def run(lib: RuleLibrary, idea: str, episodes: int, duration: int,
        demo: str | None = None) -> dict:
    if demo:
        _disable_repair()
    telemetry = InMemoryTelemetry()
    runtime = _build_runtime(
        "mock", telemetry,
        inject_violations=demo == "violations",
        inject_tier2=demo == "tier2",
        inject_fakes=demo == "evidence",
    )
    app = build_graph(use_checkpoint=demo is None)
    initial = {
        "brief": {"raw_idea": idea, "target_episodes": episodes,
                  "target_duration_sec": duration, "locked_assets": []},
        "workspace": str(Path(__file__).resolve().parent.parent / "规则库"),
        "episodes": [], "findings": [], "unclaimed": [], "trace": [], "budgets": {},
    }
    config = {"configurable": {"lib": lib, "runtime": runtime, "thread_id": "verify"},
              "recursion_limit": 300}
    final = app.invoke(initial, config)
    report = final.get("final_report") or {}
    if not report:
        raise RuntimeError(f"流水线被中断：{idea} ep={episodes} dur={duration} demo={demo}")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="引擎回归门")
    parser.add_argument("--quick", action="store_true", help="只跑对照 + 一组干净组合")
    args = parser.parse_args()

    lib = RuleLibrary.load(str(Path(__file__).resolve().parent.parent / "规则库"))
    failures: list[str] = []
    matrix = [(4, 180)] if args.quick else CLEAN_MATRIX

    print("=" * 78)
    print("① 干净路径（带修复回路）—— 预期 error=0")
    for idea_label, idea in IDEAS:
        for eps, dur in matrix:
            report = run(lib, idea, eps, dur)
            out = report["validation"]["outstanding"]
            ok = out["errors"] == 0
            if not ok:
                failures.append(
                    f"干净 {idea_label} ep={eps} dur={dur}: error={out['errors']} "
                    f"{sorted(out['by_rule'].items())}")
            print(f"  [{'OK' if ok else 'FAIL'}] {idea_label:<6} ep={eps:<3} dur={dur:<4}"
                  f" error={out['errors']} warning={out['warnings']}"
                  f" claims→judge={report['budgets']['llm_calls']}")

    print("-" * 78)
    print("② 对照一 --violations-demo —— 预期 Tier-1 命中 K/R/CH/RL/FC 五族")
    report = run(lib, IDEAS[0][1], 6, 180, demo="violations")
    out = report["validation"]["outstanding"]
    families = {fam for r in out["by_rule"]
                for fam in [_LEADING_ALPHA.match(r).group()] if fam}
    missing = {"K", "R", "CH", "RL", "FC"} - families
    if missing:
        failures.append(f"violations-demo 缺族：{sorted(missing)}（实得 {sorted(families)}）")
    print(f"  [{'OK' if not missing else 'FAIL'}] tier1={out['tier1']}"
          f" 族={sorted(families)}")

    print("-" * 78)
    print("③ 对照二 --tier2-demo —— 预期 K06 / fake_causality 命中")
    report = run(lib, IDEAS[0][1], 6, 180, demo="tier2")
    out = report["validation"]["outstanding"]
    pats = set(report["validation"].get("fake_patterns") or {})
    ok = "K06" in out["by_rule"] and "fake_causality" in pats
    if not ok:
        failures.append(f"tier2-demo 未命中 K06/fake_causality（{out['by_rule']}）")
    print(f"  [{'OK' if ok else 'FAIL'}] K06={out['by_rule'].get('K06', 0)}"
          f" patterns={sorted(pats)}")

    print("-" * 78)
    print("④ 对照三 --evidence-demo —— 预期命中全部 7 种伪证模式（带正文证据）")
    report = run(lib, IDEAS[0][1], 6, 180, demo="evidence")
    pats = set(report["validation"].get("fake_patterns") or {})
    missing_pats = ALL_FAKE_PATTERNS - pats
    if missing_pats:
        failures.append(f"evidence-demo 未覆盖：{sorted(missing_pats)}")
    no_evidence = [f for f in (report["validation"]["outstanding"].get("details") or [])
                   if f.get("tier") == 2 and f.get("pattern") and not f.get("evidence")]
    if no_evidence:
        failures.append(f"evidence-demo 有 {len(no_evidence)} 条裁判结论缺正文证据")
    print(f"  [{'OK' if not missing_pats else 'FAIL'}] "
          + " ".join(f"{p}×{report['validation']['fake_patterns'][p]['count']}"
                     for p in sorted(pats)))
    if missing_pats:
        print(f"         缺失：{sorted(missing_pats)}")

    print("=" * 78)
    if failures:
        print(f"回归门未通过（{len(failures)} 项）：")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("回归门通过：干净路径 error=0，三条对照全部命中预期，7 种伪证模式全覆盖。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
