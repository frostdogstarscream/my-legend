"""Scan handoff and referenced real evidence without printing secret values."""
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    paths = [ROOT/"docs/handoff", ROOT/"tools/handoff", ROOT/"README.md", ROOT/".gitignore",
             ROOT/"docs/phase11/DEPLOYMENT.md", ROOT/"6-实测产物/operations_report_20261014",
             ROOT/"6-实测产物/phase11_live_20261015_v3"]
    for name in ("round_1_create", "special_line", "special_continue_again"):
        paths.append(ROOT/"6-实测产物/operations_live_v8_20261014"/name)
    files = {file for path in paths for file in (path.rglob("*") if path.is_dir() else [path])
             if file.is_file() and "__pycache__" not in str(file) and file.suffix not in (".pyc",)}
    secrets = [os.environ.get(name,"") for name in ("HANDOFF_SCAN_SECRET", "DRAMA_LLM_API_KEY", "STORY_LLM_API_KEY")]
    secrets = [value for value in secrets if value]
    issues = []
    for file in files:
        text = file.read_text(encoding="utf-8")
        categories = []
        if any(value in text for value in secrets):
            categories.append("configured credential")
        if re.search(r"postgresql(?:\+\w+)?://[A-Za-z0-9_]+:[A-Za-z0-9_.%~-]+@",text):
            categories.append("database password literal")
        if re.search(r"\b(?:192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+)\b",text):
            categories.append("private address")
        if re.search(r"\bsk-[A-Za-z0-9_-]{24,}\b",text):
            categories.append("credential pattern")
        if categories:
            issues.append({"path":file.relative_to(ROOT).as_posix(),"categories":categories})
    assert not issues, issues
    result = {"files_checked":len(files), "configured_credential_available_for_exact_match":bool(secrets),
              "credential_match_count":0,"database_password_literals":0,"private_addresses":0,
              "scope":"new handoff/scripts, modified legacy Markdown and referenced real model evidence; no secret values printed",
              "passed":True}
    (ROOT/"docs/handoff/contracts/security-check.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"Sensitive data scan passed: {len(files)} files; secret values not printed")


if __name__ == "__main__":
    main()
