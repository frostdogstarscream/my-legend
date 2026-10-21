from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from .content import import_handoff, snapshot_source
from .contracts import PerformanceDocument
from .graph import Pipeline
from .io import file_hash, load, save, safe_config, safe_id
from .media import store_raw
from .performance import FIELDS
from .instructions import validate_instruction


class FileUpstream:
    """Connect the script colleague's already generated result; no new writing engine."""
    def __init__(self, path: Path):
        self.path = path

    def script(self, request: dict):
        return load(self.path)


def main():
    parser = argparse.ArgumentParser(description="Generic audio production with persistent review-gated LangGraph execution")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--input", type=Path, required=True)
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--upstream-result", type=Path)
    run.add_argument("--root", type=Path, required=True)
    run.add_argument("--thread", required=True)
    for name in ("status", "review", "continue", "tasks"):
        p = sub.add_parser(name)
        p.add_argument("--root", type=Path, required=True)
        p.add_argument("--thread", required=True)
        if name == "review":
            p.add_argument("--decision", type=Path, required=True)
    recover = sub.add_parser("recover")
    recover.add_argument("--root", type=Path, required=True)
    recover.add_argument("--task", required=True, help="raw_... identity from failures.json or task ledger")
    recover.add_argument("--file", type=Path)
    recover.add_argument("--kind", choices=["audio", "performance"], default="audio")
    recover.add_argument("--confirm-not-executed", action="store_true")
    imp = sub.add_parser("import-handoff")
    imp.add_argument("--input", type=Path, required=True)
    imp.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "import-handoff":
        data, record = snapshot_source(args.input)
        script = import_handoff(data)
        script["source"]["import_record"] = record
        save(args.output, script)
        print(json.dumps({"output": str(args.output.resolve()), "source_version_id": script["source_version_id"]}, ensure_ascii=False))
        return
    upstream = FileUpstream(args.upstream_result) if args.command == "run" and args.upstream_result else None
    pipeline = Pipeline(args.root, upstream=upstream)
    try:
        if args.command == "run":
            data, record = snapshot_source(args.input)
            safe_config(data)
            source_copy = pipeline.root / "sources" / ("input_" + record["file_sha256"] + ".json")
            source_copy.parent.mkdir(parents=True, exist_ok=True)
            if not source_copy.exists():
                shutil.copyfile(args.input, source_copy)
            record["snapshot_file"] = source_copy.relative_to(pipeline.root).as_posix()
            pipeline.run(args.thread, data, load(args.config), args.input.parent, record)
        elif args.command == "review":
            pipeline.resume(args.thread, load(args.decision))
        elif args.command == "continue":
            pipeline.continue_failed(args.thread)
        elif args.command == "tasks":
            print(json.dumps(pipeline.registry.list(), ensure_ascii=False, indent=2))
            return
        elif args.command == "recover":
            safe_id(args.task)
            if bool(args.file) == bool(args.confirm_not_executed):
                raise ValueError("Choose recovered file OR confirmation that no result was produced")
            if args.file:
                if args.kind == "performance":
                    data = load(args.file)
                    safe_config(data)
                    if args.task.startswith("text_line_"):
                        if set(data) != {"line", "generation_record"} or not isinstance(data["generation_record"], dict):
                            raise ValueError("Per-line recovery requires line and generation_record")
                        line = data["line"]
                        if not {"text", "role_id", "scene_id", "context", "performance"} <= set(line) or not FIELDS <= set(line["performance"]):
                            raise ValueError("Incomplete per-line recovery artifact")
                        validate_instruction(line["performance"].get("instructions"), line.get("instruction_policy", {}).get("max_weight", 240))
                        data["generation_record"] = data["generation_record"] | {"origin": "operator_recovery", "source_sha256": file_hash(args.file)}
                    else:
                        PerformanceDocument.model_validate(data)
                    path = pipeline.root / "designs" / (args.task + ".json")
                    save(path, data)
                    recovered = {"file": path.relative_to(pipeline.root).as_posix(), "sha256": file_hash(path), "origin": "operator_recovery"}
                else:
                    recovered = store_raw(pipeline.root, args.file) | {"origin": "operator_recovery", "semantic_review": "pending"}
                pipeline.registry.recover(args.task, recovered)
            else:
                pipeline.registry.confirm_not_executed(args.task)
            print(json.dumps(pipeline.registry.get(args.task), ensure_ascii=False))
            return
        status = pipeline.status(args.thread)
        info = status.get("preview") or {}
        print(json.dumps({k: status[k] for k in ("status", "next", "revision", "problems", "call_budget")} | {"preview": info.get("preview"), "review_bundle_sha256": info.get("bundle", {}).get("bundle_sha256"), "technical_passed": info.get("checks", {}).get("technical_passed"), "duration_ms": (info.get("checks", {}).get("mix") or {}).get("duration_ms"), "delivery": status.get("delivery")}, ensure_ascii=False, indent=2))
    except Exception as exc:
        # No raw provider response, URL, request headers or traceback is printed.
        print(json.dumps({"error_type": type(exc).__name__, "message": "Operation stopped; inspect the structured state/ledger. No publication performed unless status is delivered."}, ensure_ascii=False))
        raise SystemExit(1) from None
    finally:
        pipeline.close()


if __name__ == "__main__":
    main()
