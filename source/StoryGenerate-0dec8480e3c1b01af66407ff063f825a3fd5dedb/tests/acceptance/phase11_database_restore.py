"""Restore a completed acceptance schema into an isolated PostgreSQL16 database.

Requires STORY_DATABASE_URL and the project's PostgreSQL Docker container.
Passwords remain in environment; no connection URL or dump is committed.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import uuid
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--container", default="storygenerate-pg")
    args = parser.parse_args()
    schema = json.loads(args.manifest.read_text(encoding="utf-8"))["schema"]
    if not re.fullmatch(r"phase11_live_[0-9a-f]{12}", schema):
        raise ValueError("NOT_AN_ACCEPTANCE_SCHEMA")
    database = "phase11_restore_" + uuid.uuid4().hex[:12]
    dump = "/tmp/" + database + ".dump"
    url = make_url(os.environ["STORY_DATABASE_URL"])

    def docker(*command):
        subprocess.run(
            ["docker", "exec", args.container, *command],
            check=True,
            capture_output=True,
        )

    docker(
        "pg_dump",
        "-U",
        url.username,
        "-d",
        url.database,
        "-n",
        schema,
        "-Fc",
        "-f",
        dump,
    )
    docker("createdb", "-U", url.username, database)
    docker("pg_restore", "-U", url.username, "-d", database, "--exit-on-error", dump)

    def snapshot(connection_url):
        engine = create_engine(
            connection_url, connect_args={"options": "-csearch_path=" + schema}
        )
        tables = (
            "character_templates",
            "character_profile_revisions",
            "story_plans",
            "story_plan_revisions",
            "story_jobs",
            "story_versions",
            "service_plan_tasks",
        )
        with engine.connect() as connection:
            counts = {
                table: connection.execute(
                    text("SELECT count(*) FROM " + table)
                ).scalar_one()
                for table in tables
            }
            # Hash every row, including approval snapshots, usage, leases and full delivery.
            hashes = {}
            for table in tables:
                rows = (
                    connection.execute(
                        text("SELECT row_to_json(t)::text FROM " + table + " t")
                    )
                    .scalars()
                    .all()
                )
                canonical = sorted(
                    json.dumps(json.loads(row), ensure_ascii=False, sort_keys=True)
                    for row in rows
                )
                hashes[table] = hashlib.sha256(
                    "\n".join(canonical).encode()
                ).hexdigest()
        engine.dispose()
        return {"counts": counts, "row_hashes": hashes}

    before = snapshot(url)
    restored = snapshot(url.set(database=database))
    record = {
        "schema": schema,
        "restored_database": database,
        "backup_format": "pg_dump custom",
        "source": before,
        "restored": restored,
        "passed": before == restored,
        "retained_in_container": True,
        "credentials_in_artifacts": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    assert record["passed"], "DATABASE_RESTORE_MISMATCH"
    print("PostgreSQL backup/restore: all table counts and full-row hashes match")


if __name__ == "__main__":
    main()
