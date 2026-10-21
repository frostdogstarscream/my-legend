"""Phase 4.5E: Controlled Provider Parallel Verification.

用法:
    $env:MOCK_LLM_LATENCY_SEC="2"
    python scripts/bench_controlled_parallel.py

验证:
  - parallel_ledger_plan 的 ledger 和 plan_draft branch 是否时间窗口重叠
  - sequential vs parallel wall time 差异
"""
import os, sys, time, json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "4-引擎代码"))

from drama_engine.graph import run_pipeline
from drama_engine.config import RuleLibrary
from drama_engine.contracts import InMemoryTelemetry, Runtime
from drama_engine.llm.latency_controlled import (
    LatencyControlledMockProvider, verify_parallel_overlap,
)
from drama_engine.mocks import MockMarketRetriever, MockTTSRenderer


def run(mode: str, parallel_enabled: bool, delay_sec: float, episodes: int):
    os.environ["STORY_PARALLEL_ENABLED"] = str(parallel_enabled).lower()
    os.environ["MOCK_LLM_LATENCY_SEC"] = str(delay_sec)

    lib = RuleLibrary.load()
    tele = InMemoryTelemetry()
    provider = LatencyControlledMockProvider(delay_sec=delay_sec)
    provider._telemetry = tele
    rt = Runtime(llm=provider, tts=MockTTSRenderer(), market=MockMarketRetriever(),
                 telemetry=tele)

    t0 = time.perf_counter()
    r = run_pipeline(
        "深夜仓库中的货物每天都会少一件，但监控里从没有人进出。",
        ".", lib, rt,
        target_episodes=episodes,
        target_duration_sec=90,
        thread_id=f"bench-ctr-{mode}",
    )
    wall = time.perf_counter() - t0

    s = tele.summary()
    calls = s.get("total_calls", 0)

    # 检查 parallel overlap
    timestamps = provider.call_timestamps
    ledger_calls = [c for c in timestamps if "ledger" in c.get("node", "")]
    plan_calls = [c for c in timestamps if "plan" in c.get("node", "")]
    overlap, overlap_ms = verify_parallel_overlap(timestamps, "ledger", "plan")

    # 检查 parallel group events
    parallel_saved = 0
    for ev in getattr(tele, "events", []) or []:
        if isinstance(ev, dict) and ev.get("type") == "parallel_group":
            parallel_saved = ev.get("parallel_saved_ms", 0)

    return {
        "mode": mode,
        "parallel_enabled": parallel_enabled,
        "wall_s": round(wall, 2),
        "llm_calls": calls,
        "ledger_calls": len(ledger_calls),
        "plan_calls": len(plan_calls),
        "overlap": overlap,
        "overlap_ms": round(overlap_ms, 1),
        "parallel_saved_ms": parallel_saved,
        "total_timestamps": len(timestamps),
        "_debug_timestamps": [
            {"node": c["node"], "start": round(c["started_at"] - t0, 3),
             "end": round(c["finished_at"] - t0, 3)}
            for c in timestamps
        ],
    }


if __name__ == "__main__":
    delay = float(os.environ.get("MOCK_LLM_LATENCY_SEC", "2"))
    eps = int(os.environ.get("BENCH_EPISODES", "2"))

    print(f"Phase 4.5E Controlled Provider Benchmark")
    print(f"  delay={delay}s  episodes={eps}")
    print()

    # Run sequential
    print("--- SEQUENTIAL ---")
    seq = run("sequential", parallel_enabled=False, delay_sec=delay, episodes=eps)
    print(json.dumps(seq, indent=2))

    # Run parallel
    print("--- PARALLEL ---")
    par = run("parallel", parallel_enabled=True, delay_sec=delay, episodes=eps)
    print(json.dumps(par, indent=2))

    # Comparison
    improvement = (seq["wall_s"] - par["wall_s"]) / seq["wall_s"] * 100 if seq["wall_s"] > 0 else 0
    print()
    print("=== COMPARISON ===")
    print(f"  Sequential wall: {seq['wall_s']:.1f}s")
    print(f"  Parallel wall:   {par['wall_s']:.1f}s")
    print(f"  Improvement:     {improvement:.0f}%")
    print(f"  Overlap:         {par['overlap']} ({par['overlap_ms']:.0f}ms)")
    print(f"  Target:          >=20% wall reduction + overlap=True")
    print(f"  RESULT:          {'PASS' if improvement >= 20 and par['overlap'] else 'FAIL'}")