"""Process-level M1 verification with labelled module fixtures and real PostgreSQL.

This command never calls a model and is not production media acceptance.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from uuid import uuid4

import psycopg
from psycopg import sql

from my_legend_production.production.artifacts import ArtifactStore
from my_legend_production.production.contracts import ProductionRequest, StageResult, REQUIRED_ARTIFACTS
from my_legend_production.production.repository import ProductionRepository
from my_legend_production.production.worker import ProductionWorker


def fixture_handler(ctx):
    refs = [ctx.store.put_json(kind, "fixture-version-1", {"fixture_only": True, "kind": kind})
            for kind in sorted(REQUIRED_ARTIFACTS[ctx.stage["name"]])]
    return StageResult(status="succeeded", source_version_id="fixture-version-1", artifacts=refs,
                       technical_quality="passed", poll_after_seconds=0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--ticks", type=int, default=1)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--schema")
    args = parser.parse_args()
    dsn = os.environ["MY_LEGEND_PRODUCTION_TEST_DSN"]
    if args.child:
        if not args.schema.startswith("legend_prod_verify_"):
            raise RuntimeError("TEST_NAMESPACE_REQUIRED")
        worker = ProductionWorker(ProductionRepository(dsn, args.schema), ArtifactStore(args.root / "artifacts"),
                                  {k: fixture_handler for k in REQUIRED_ARTIFACTS})
        for _ in range(args.ticks):
            worker.tick()
        return
    workspace = Path(__file__).resolve().parents[2]
    root = Path(tempfile.mkdtemp(prefix="verify-orchestrator-", dir=workspace / "runtime/my-legend-story"))
    schema = "legend_prod_verify_" + uuid4().hex
    repo = ProductionRepository(dsn, schema)
    repo.migrate()
    try:
        request = ProductionRequest.model_validate({"idempotency_key": "process-restart-test", "source": {
            "kind": "creative", "title": "隔离编排验证", "idea": "仅测试编排，不生成故事或媒体。", "genre": "测试", "character_ids": ["fixture"]},
            "outputs": ["script", "audio", "visual"]})
        job_id = repo.create("verification", request)
        command = [sys.executable, str(Path(__file__).resolve()), "--child", "--root", str(root), "--schema", schema]
        first = subprocess.run(command + ["--ticks", "1"], check=True, timeout=30)
        # First process exits after committing story. A distinct process resumes the remaining DAG.
        second = subprocess.run(command + ["--ticks", "4"], check=True, timeout=30)
        state = repo.get(job_id, "verification")
        assert state["status"] == "succeeded"
        assert all(s["attempts"] == 1 for s in state["stages"])
        assert state["actual_calls"] == state["reserved_calls"] == 0
        report = {"fixture_only": True, "real_media": False, "provider_calls": 0, "status": state["status"],
                  "processes_used": 2, "frontend_or_status_polling_during_execution": False,
                  "stage_attempts": {s["name"]: s["attempts"] for s in state["stages"]}, "artifact_count": len(state["artifacts"])}
        (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({"report": str(root / "report.json"), **report}, ensure_ascii=False))
    finally:
        assert schema.startswith("legend_prod_verify_") and len(schema) == 51
        with psycopg.connect(dsn) as db:
            db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


if __name__ == "__main__":
    main()
