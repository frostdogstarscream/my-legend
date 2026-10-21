"""Recompute cost from physical traces; never call a model or assume prices."""
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def metrics(calls):
    unique = {c["call_id"]:c for c in calls}
    reported = [c for c in unique.values() if c.get("usage_source") == "reported"]
    return {"calls":len(unique), "input":sum(c.get("input_tokens",0) for c in reported),
            "output":sum(c.get("output_tokens",0) for c in reported),
            "reasoning_in_output":sum(c.get("reasoning_tokens",0) for c in reported),
            "cache_read":sum(c.get("cache_read_tokens",0) for c in reported),
            "cache_write":sum(c.get("cache_write_tokens",0) for c in reported),
            "cache_read_calls":sum(c.get("cache_read_tokens",0)>0 for c in reported),
            "estimated_input":sum(c.get("input_tokens",0) for c in unique.values() if c.get("usage_source")=="estimated"),
            "unknown_usage_calls":sum(c.get("usage_source")=="unavailable" for c in unique.values()),
            "model_seconds":round(sum(c.get("latency_ms",0) for c in unique.values())/1000,3)}


def group(calls, field):
    grouped = defaultdict(list)
    for c in calls:
        grouped[c.get(field) or "unknown"].append(c)
    return {key:metrics(value) for key,value in sorted(grouped.items())}


def main():
    original = load(ROOT/"6-实测产物/operations_report_20261014/acceptance.json")
    p10_all, batches = [], []
    for record in original["batches"]:
        folder = ROOT/record["batch"].replace("\\", "/")
        files = [*folder.glob("*/calls.json"), *folder.glob("attempts/*/calls.json")]
        if (folder/"calls.json").exists():
            files.append(folder/"calls.json")
        calls = [c for file in files for c in load(file)]
        p10_all.extend(calls)
        if calls:
            attempts = [*record["results"], *record.get("earlier_attempts", [])]
            batches.append({"batch":folder.name, "usage":metrics(calls),
                "executed_attempts":sum(r["status"] in ("passed","failed") for r in attempts),
                "failed_attempts":sum(r["status"]=="failed" for r in attempts),
                "manifest_wall_seconds":record.get("manifest",{}).get("acceptance_wall_time_sec",record.get("manifest",{}).get("wall_time_sec"))})
    latest = ROOT/"6-实测产物/operations_live_v8_20261014"
    case_costs = []
    for file in sorted(latest.glob("*/result.json")) + sorted(latest.glob("attempts/*/result.json")):
        result = load(file)
        cf = file.parent/"calls.json"
        case_costs.append({"case":file.parent.name, "status":result["status"],
                          "source":file.relative_to(ROOT).as_posix(),
                          "usage":metrics(load(cf) if cf.exists() else []),
                          "wall_seconds":result.get("wall_time_sec"),
                          "failure":result.get("error",result.get("reason"))})
    p11 = {}
    for batch in ("phase11_live_20261015_v1", "phase11_live_20261015_v2", "phase11_live_20261015_v3"):
        summary = load(ROOT/"6-实测产物"/batch/"summary.json")
        for resource, detail in summary["resources"].items():
            for call in detail["call_records"]:
                tagged = dict(call, operation=detail["operation"], phase=detail["phase"], resource_id=resource)
                if call["call_id"] in p11:
                    assert p11[call["call_id"]] == tagged
                p11[call["call_id"]] = tagged
    # Verify summary traces against raw provider captures, including failed/retried captures.
    raw = {}
    for batch in ("phase11_live_20261015_v1", "phase11_live_20261015_v2", "phase11_live_20261015_v3"):
        for file in (ROOT/"6-实测产物"/batch/"model_calls").rglob("call_*.json"):
            for call in load(file).get("calls", []):
                raw[call["call_id"]] = call
    assert set(raw) == set(p11), "RAW_CAPTURE_COVERAGE_MISMATCH"
    for cid, call in raw.items():
        for field in ("input_tokens", "output_tokens", "reasoning_tokens", "latency_ms", "actual_model"):
            assert call.get(field) == p11[cid].get(field), (cid,field)
    old11 = load(ROOT/"6-实测产物/phase11_engineering/model_statistics.json")
    total11 = metrics(p11.values())
    assert total11["calls"] == 75 and total11["input"]+total11["output"] == 1317167
    total10 = metrics(p10_all)
    assert total10["input"] == original["all_attempts_usage"]["input"]
    assert total10["output"] == original["all_attempts_usage"]["output"]
    report = {"method":"global physical call_id dedup per phase; raw capture comparison for Phase11; reasoning already inside output; no new model calls",
        "phase10":{"all_history":total10, "batches":batches, "latest_cases":case_costs,
                   "all_by_stage":group(p10_all,"node"), "all_by_model":group(p10_all,"actual_model"),
                   "latest_by_operation":original["latest_by_operation"], "latest_by_round":original["latest_by_round"],
                   "latest_by_stage":original["latest_by_stage"],
                   "potential_inflight_unknown_nodes":original["potential_inflight_usage_unknown_nodes"]},
        "phase11":{"total":total11, "by_operation":group(p11.values(),"operation"), "by_phase":group(p11.values(),"phase"),
                   "by_stage":group(p11.values(),"node"), "by_model":group(p11.values(),"actual_model"),
                   "by_resource":group(p11.values(),"resource_id"), "generation_failures":old11["generation_failures"],
                   "resource_wall_seconds":old11["resource_end_to_end_wall"],
                   "wall_seconds_all_invocations":old11["wall_seconds_all_invocations"],
                   "raw_capture_call_ids_match":True},
        "pricing":"No supplier invoice or current rates available. No currency cost claimed."}
    path = ROOT/"docs/handoff/contracts/cost-recomputed.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 成本逐项复算表", "", "程序从归档calls按call_id去重；完整字段和失败原因见[cost-recomputed.json](cost-recomputed.json)。所有耗时单位秒。reasoning属于output子集，不另加；cache读取是input中的供应商单列信息。费用未知。", ""]
    def table(title, rows):
        lines.extend([f"## {title}", "", "|分组|调用|Input|Output|Reasoning已含|缓存读|有缓存读调用|估算Input|未知用量调用|模型请求秒|", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"])
        for key,v in rows.items():
            lines.append("|"+str(key)+"|"+"|".join(str(v.get(f,"见原统计")) for f in ("calls","input","output","reasoning_in_output","cache_read","cache_read_calls","estimated_input","unknown_usage_calls","model_seconds"))+"|")
        lines.append("")
    table("Phase10历史批次（包括预检/负向调用）", {v["batch"]:v["usage"] for v in batches})
    for key in ("latest_by_operation", "latest_by_round", "latest_by_stage", "all_by_model"):
        table("Phase10 "+key, report["phase10"][key])
    for key in ("by_operation", "by_phase", "by_stage", "by_model", "by_resource"):
        table("Phase11 "+key, report["phase11"][key])
    lines += ["## Phase10最终批次每次尝试", "", "|案例|状态|Input/Output|模型秒|壁钟秒|", "|---|---|---:|---:|---:|"]
    for case in case_costs:
        usage = case["usage"]
        lines.append(f"|{case['case']}|{case['status']}|{usage['input']}/{usage['output']}|{usage['model_seconds']}|{case['wall_seconds']}|")
    lines += ["", "## Phase11三次验收运行", "", "下表按调用首次出现的运行分摊，恢复运行复制的历史trace不再次收费。运行wall仍包含其重新读取/校验时间，不能当作独立母故事成功率。", "", "|运行|新物理调用|新Input/Output|运行壁钟秒|", "|---|---:|---:|---:|"]
    seen = set()
    for batch in ("phase11_live_20261015_v1", "phase11_live_20261015_v2", "phase11_live_20261015_v3"):
        summary = load(ROOT/"6-实测产物"/batch/"summary.json")
        calls = {c["call_id"]:c for r in summary["resources"].values() for c in r["call_records"]}
        fresh = metrics([c for cid,c in calls.items() if cid not in seen])
        seen.update(calls)
        lines.append(f"|{batch}|{fresh['calls']}|{fresh['input']}/{fresh['output']}|{round(summary['wall_seconds'],3)}|")
    (path.parent/"cost-tables.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    print(f"Recomputed Phase10 {total10['calls']} calls; Phase11 {total11['calls']} calls; raw capture match")


if __name__ == "__main__":
    main()
