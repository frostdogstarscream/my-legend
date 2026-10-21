"""Build auditable acceptance reports from retained batches and JUnit evidence."""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import xml.etree.ElementTree as ET


def read(path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def test_result(path):
    if not path.exists():
        return {"passed": False, "missing": True}
    suites = ET.parse(path).getroot().iter("testsuite")
    total = failures = errors = skipped = 0
    seconds = 0.0
    for suite in suites:
        total += int(suite.get("tests", 0))
        failures += int(suite.get("failures", 0))
        errors += int(suite.get("errors", 0))
        skipped += int(suite.get("skipped", 0))
        seconds += float(suite.get("time", 0))
    return {"tests": total, "failures": failures, "errors": errors, "skipped": skipped,
            "seconds": seconds, "passed": total > 0 and failures == errors == skipped == 0}


def token_stats(calls):
    unique = {c["call_id"]: c for c in calls}
    reported = [c for c in unique.values() if c.get("usage_source") == "reported"]
    return {"calls": len(unique), "input": sum(c.get("input_tokens", 0) for c in reported),
            "output": sum(c.get("output_tokens", 0) for c in reported),
            "reasoning_in_output": sum(c.get("reasoning_tokens", 0) for c in reported),
            "cache_read": sum(c.get("cache_read_tokens", 0) for c in reported),
            "estimated_input": sum(c.get("input_tokens", 0) for c in unique.values() if c.get("usage_source") == "estimated"),
            "unknown_usage_calls": sum(c.get("usage_source") == "unavailable" for c in unique.values()),
            "model_seconds": round(sum(c.get("latency_ms", 0) for c in unique.values()) / 1000, 3)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", action="append", type=Path, required=True)
    parser.add_argument("--engineering", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    unit = test_result(args.engineering / "unit.xml")
    pg = test_result(args.engineering / "postgres.xml")
    current = args.batch[-1]
    manifest = read(current / "manifest.json", {})
    code_verification = read(current / 'code_verification.json', {})
    implementation_verified = not manifest.get('tracked_dirty', True) or (
        code_verification.get('implementation_sha') == manifest.get('git_sha')
        and code_verification.get('engine_and_acceptance_sources_match_commit') is True
        and code_verification.get('tracked_changes_at_launch_and_verification') == ['scripts/generate_operations_report.py'])
    all_calls, current_results, histories = [], [], []
    potential_inflight_unknown_nodes = 0
    by_stage = defaultdict(list)
    for batch in args.batch:
        results = [read(p) | {"directory": str(p.parent.resolve())} for p in sorted(batch.glob("*/result.json"))]
        attempts = [read(p) | {"directory": str(p.parent.resolve())} for p in sorted(batch.glob("attempts/*/result.json"))]
        histories.append({"batch": str(batch), "manifest": read(batch / "manifest.json", {}), "results": results, "earlier_attempts": attempts})
        potential_inflight_unknown_nodes += read(batch / "manifest.json", {}).get("potential_inflight_usage_unknown_nodes", 0)
        if batch == current:
            current_results = results
        if (batch / "calls.json").exists():
            extra_calls = read(batch / "calls.json", [])
            all_calls.extend(extra_calls)
            for call in extra_calls:
                by_stage[call.get("node", "unknown")].append(call)
        for path in [*batch.glob("*/calls.json"), *batch.glob("attempts/*/calls.json")]:
            calls = read(path, [])
            all_calls.extend(calls)
            for call in calls:
                by_stage[call.get("node", "unknown")].append(call)
    review = read(current / "auditor_review.json", {})
    statuses = {}
    expected_specials = {"create": [], "revise": ["special_line", "special_episode", "special_arc", "special_ending", "special_whole_story"],
                         "continue": ["special_continue_again"], "remix": []}
    rounds = manifest.get("rounds", 3)
    for op in ("create", "revise", "continue", "remix"):
        labels = [f"round_{n}_{op}" for n in range(1, rounds + 1)] + expected_specials[op]
        selected = [r for r in current_results if r["label"] in labels]
        automation_passed = len(selected) == len(labels) and all(r["status"] == "passed" for r in selected)
        tested = unit["passed"] and pg["passed"]
        reviewed = all(review.get(r["label"], {}).get("passed") is True for r in selected) and bool(selected)
        real_passed = automation_passed and reviewed
        statuses[op] = {"implemented": True, "tested": tested, "real_model_passed": real_passed,
                        "artifact_review_passed": reviewed,
                        "deliverable": tested and real_passed and reviewed and implementation_verified,
                        "expected": len(labels), "passed": sum(r["status"] == "passed" for r in selected)}
        lines = [f"# {op.capitalize()} 专项验收报告", "", f"实现提交：`{manifest.get('git_sha', 'unknown')}`。", "",
                 "| 已实现 | 测试通过 | 真实模型通过 | 具备产品交付条件 |", "| --- | --- | --- | --- |",
                 "| " + " | ".join("是" if statuses[op][k] else "否／未满足" for k in
                                     ("implemented", "tested", "real_model_passed", "deliverable")) + " |", "",
                 f"最终批次通过 {statuses[op]['passed']}/{len(labels)} 项；工程单元 {unit.get('tests', 0)} 项、PostgreSQL {pg.get('tests', 0)} 项。", "",
                 "| 案例 | 结果 | Input / Output Token | 壁钟秒 | 产物 |", "| --- | --- | ---: | ---: | --- |"]
        for r in selected:
            lines.append(f"| {r['label']} | {r['status']} | {r.get('reported_input_tokens', '未知')} / {r.get('reported_output_tokens', '未知')} | {r.get('wall_time_sec', '未执行')} | [证据]({Path(r['directory']).as_posix()}/result.json) |")
        for label in labels:
            entry = next((r for r in selected if r["label"] == label), None)
            if not entry:
                lines.extend(["", f"{label}：缺少执行证据。"])
            elif entry["status"] != "passed":
                lines.extend(["", f"{label}：{entry.get('error') or entry.get('reason', '未通过')}。"])
        lines.extend(["", "## 原始产物复核", ""])
        for r in selected:
            findings = review.get(r["label"], {}).get("findings", ["尚无独立产物复核记录"])
            lines.extend([f"- {r['label']}：" + "；".join(findings)])
        lines.extend(["", "## 逐项模型证据及定位", ""])
        for r in selected:
            delivery = read(Path(r["directory"]) / "delivery.json", {})
            for check in r.get("operation_checks", []):
                locations = []
                for quote in check.get("evidence_quotes", []):
                    matches = [f"第{ep['index']}集/{scene['scene_id']}/{u['line_id']}" for ep in delivery.get("episodes", [])
                               for scene in ep["scenes"] for u in scene["utterances"] if quote in u["text"]]
                    locations.append(f"“{quote}” ({', '.join(matches) or '跨行引文，见正文'})")
                lines.append(f"- {r['label']} / {check['requirement_id']}：{check['passed']}；" + "；".join(locations))
        lines.extend(["", "单元与数据库测试使用显式测试替身，真实模型状态仅由本批次真实 Provider 产物判定。三轮样本不代表规模化稳定性。", ""])
        (args.output / f"{op.upper()}_REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    stats = token_stats(all_calls)
    latest_calls = [c for path in [*current.glob("*/calls.json"), *current.glob("attempts/*/calls.json")]
                    for c in read(path, [])]
    latest_stats = token_stats(latest_calls)
    attempted = [r for r in current_results + histories[-1]["earlier_attempts"] if r["status"] in ("passed", "failed")]
    failed_attempts = sum(r["status"] == "failed" for r in attempted)
    historical_attempts = [r for h in histories for r in h['results'] + h['earlier_attempts']
                           if r['status'] in ('passed', 'failed')]
    historical_failures = sum(r['status'] == 'failed' for r in historical_attempts)
    latest_by_operation, latest_by_round, latest_by_stage = defaultdict(list), defaultdict(list), defaultdict(list)
    for r in current_results + histories[-1]['earlier_attempts']:
        calls = read(Path(r['directory']) / 'calls.json', [])
        latest_by_operation[r['intent']].extend(calls)
        if r['label'].startswith('round_'):
            latest_by_round['_'.join(r['label'].split('_')[:2])].extend(calls)
    for call in latest_calls:
        latest_by_stage[call.get('node', 'unknown')].append(call)
    result = {"manifest": manifest, "code_verification": code_verification,
              "implementation_verified": implementation_verified, "unit": unit, "postgres": pg, "statuses": statuses,
              "potential_inflight_usage_unknown_nodes": potential_inflight_unknown_nodes,
              "all_attempts_usage": stats, "latest_batch_usage": latest_stats,
              "latest_executed_attempts": len(attempted), "latest_failed_attempts": failed_attempts,
              "historical_executed_attempts": len(historical_attempts), "historical_failed_attempts": historical_failures,
              "latest_by_operation": {k: token_stats(v) for k, v in latest_by_operation.items()},
              "latest_by_round": {k: token_stats(v) for k, v in latest_by_round.items()},
              "latest_by_stage": {k: token_stats(v) for k, v in latest_by_stage.items()},
              "by_stage": {k: token_stats(v) for k, v in by_stage.items()},
              "batches": histories, "history_recheck": read(args.engineering / "history_recheck.json", [])}
    (args.output / "acceptance.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# StoryGenerate 四操作验收总报告", "", f"最终实现提交：`{manifest.get('git_sha', 'unknown')}`。",
             "引擎与结构化故事交付；不含实际 TTS、HTTP 和硬件播放。", "",
             "| 操作 | 已实现 | 测试通过 | 真实模型通过 | 产品交付条件 | 最终通过数 |",
             "| --- | --- | --- | --- | --- | ---: |"]
    for op, status in statuses.items():
        lines.append(f"| [{op.capitalize()}]({op.upper()}_REPORT.md) | 是 | {'是' if status['tested'] else '否'} | {'是' if status['real_model_passed'] else '否'} | {'满足本轮边界' if status['deliverable'] else '未满足'} | {status['passed']}/{status['expected']} |")
    lines.extend(["", f"单元 {unit.get('tests', 0)} 项、PostgreSQL {pg.get('tests', 0)} 项；失败/错误/跳过分别为 {unit.get('failures', 0) + pg.get('failures', 0)}/{unit.get('errors', 0) + pg.get('errors', 0)}/{unit.get('skipped', 0) + pg.get('skipped', 0)}。", "",
                  f"最终批次实际执行 {len(attempted)} 次操作尝试，失败 {failed_attempts} 次（{failed_attempts / max(len(attempted), 1):.1%}）；被阻塞未调用模型的阶段不进入尝试分母。成功重试不覆盖原失败。",
                  f"保留的历次操作尝试合计 {len(historical_attempts)} 次，失败 {historical_failures} 次（{historical_failures / max(len(historical_attempts), 1):.1%}），包含中断后判失败及正文复核撤销通过的案例；跨修复提交样本不能用作当前版本失败率估计。",
                  f"最终批次含失败与重试：Input {latest_stats['input']:,}、Output {latest_stats['output']:,}、合计 {latest_stats['input'] + latest_stats['output']:,} Token。原运行器壁钟 {manifest.get('wall_time_sec', '未知')} 秒；含补充执行的验收壁钟 {manifest.get('acceptance_wall_time_sec', manifest.get('wall_time_sec', '未知'))} 秒。重试/澄清独立耗时见 retry_manifest.json 和 calendar_clarification_manifest.json。",
                  "Remix 第一轮首次被模型质量门拒绝，重试自动通过但产物复核仍要求澄清日期；最终新增同晚 calendar 输入约束并重新执行原 prepare→approve→generate 链，正文由真实模型重新生成。未将模糊原稿计为最终通过，也不宣称通用相对日期推理已经形式化证明。", "",
                  "## 全部尝试的 Token 与耗时", "",
                  f"提供商报告 Input {stats['input']:,}，Output {stats['output']:,}，合计 {stats['input']+stats['output']:,} Token；共 {stats['calls']} 次请求。",
                  f"Reasoning {stats['reasoning_in_output']:,} 已包含在 Output，不再累加；缓存读取 {stats['cache_read']:,} 单列。",
                  f"本地估算 Input {stats['estimated_input']:,}；{stats['unknown_usage_calls']} 次请求用量不可获取，未记为零成本。",
                  f"另有 {potential_inflight_unknown_nodes} 个中断节点未获得最终响应，可能产生额外用量；上述 Token 是可核验下限，不能作为供应商账单总额。",
                  f"模型请求时延总和 {stats['model_seconds']:,.3f} 秒；并发运行不能将此数当作批次壁钟时长。各操作壁钟见明细，批次时间见 manifest。", "",
                  "## 历史证据修正", "",
                  "固定模板脚本的完整性失败覆盖及元组真假误判已修复。历史 T03 的 chen_mo 缺角色映射，T05 无成功正文，七个案例仅五个保存产物通过当前结构复核；这不是重新执行生成或文学质量验收。", "",
                  "## 证据与限制", "",
                  "acceptance.json 汇总所有批次、失败、阻塞与逐阶段用量。各批次保留请求、审批快照、候选、正文、模型请求响应、数据库重读、差异及持久化任务。最终通过必须同时满足工程测试、指定真实用例和独立产物复核。",
                  "原始 manifest 的 tracked_dirty 不覆盖：若仅报告统计脚本有未提交修改，另以 code_verification.json 核对引擎、测试和真实验收入口与实现提交完全一致；任何业务代码差异均不满足绑定提交要求。",
                  "三轮只能证明本批次重复验收；不得推断大规模成功率。READY 不等于真实 TTS 时长或文学盲评通过。", ""])
    lines.extend(["## 复核入口", "", f"- [调用链、Schema、事务与恢复合同]({(current.resolve().parents[1] / 'docs' / 'STORY_OPERATIONS_AUDIT.md').as_posix()})",
                  f"- [最终18项产物与原失败尝试]({(current.resolve() / 'final_summary.json').as_posix()})",
                  f"- [跨集输入与正文复核]({(current.resolve() / 'auditor_review.json').as_posix()})",
                  f"- [八个保留schema无待执行任务检查]({(current.resolve() / 'database_final_audit.json').as_posix()})",
                  f"- [工程回归原始JUnit]({(args.engineering.resolve() / 'all.xml').as_posix()})", ""])
    (args.output / "FINAL_REPORT.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
