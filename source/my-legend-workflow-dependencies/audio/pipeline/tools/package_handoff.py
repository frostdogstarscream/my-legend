"""Package implementation and an optional portable audition, excluding runtime secrets."""
from __future__ import annotations

import argparse
import json
import re
import zipfile
from pathlib import Path

from lingban_pipeline.delivery import verify_bundle
from lingban_pipeline.io import file_hash, load, save, sensitive_artifact_text, safe_id


def zip_checked(destination: Path, files: list[tuple[Path, str]]):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ValueError("Package already exists; choose a new output name")
    for path, relative in files:
        if path.suffix.lower() in {".md", ".py", ".json", ".txt", ".toml", ".html", ".xml"}:
            if sensitive_artifact_text(path.read_text(encoding="utf-8-sig")):
                raise ValueError("Credential-like content detected; package stopped")
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as z:
        for path, relative in files:
            z.write(path, relative)
    with zipfile.ZipFile(destination) as z:
        if z.testzip() is not None:
            raise ValueError("Package integrity failed")
    return {"file": destination.name, "sha256": file_hash(destination), "size_bytes": destination.stat().st_size, "member_count": len(files), "zip_integrity_passed": True}


def package(repo: Path, output: Path, demo: Path | None, label="v0.2.1_20261013"):
    safe_id(label)
    output.mkdir(parents=True, exist_ok=True)
    names = {"AGENTS.md", "contracts/handoff.md"}
    for base in (repo / "pipeline", repo / "docs"):
        for p in base.rglob("*"):
            relative = p.relative_to(repo)
            if not p.is_file() or any(part.startswith((".venv", ".test", ".pytest")) or part in {"__pycache__", ".git"} or part.endswith(".egg-info") for part in relative.parts):
                continue
            if p.suffix.lower() in {".md", ".py", ".json", ".txt", ".toml", ".xml"} or p.name == ".gitignore":
                names.add(relative.as_posix())
    files = [(repo / name, name) for name in sorted(names)]
    notice = output / "README.md"
    notice.write_text(f"# 灵伴通用 LangGraph 开发包 {label}\n\n先阅读 [新版使用文档](docs/guides/LangGraph使用文档_v0.2.0_20261013.md) 和 [升级报告](docs/reports/LangGraph升级报告_v0.2.0_20261013.md)。源码与命令位于 pipeline。\n\n本包包含通用制作线、测试、配置模板、公共 Schema 和文档，不包含旧作品媒体、运行环境、凭据或运行中的 SQLite。解压后进入 pipeline，按使用文档新建隔离环境。tools/run_offline_demo.py 可在零外部调用预算下运行合成波形示例，不自动批准发布。\n\n[协作仓库历史说明](协作仓库历史说明.md)保留作背景，其中 productions 和仓库根 tools 的历史命令不属于本开发包。新版入口以本文和 pipeline/README.md 为准。\n", encoding="utf-8")
    notice.write_text(notice.read_text(encoding="utf-8").replace("先阅读 ", "当前参数以 [v0.2.1调整说明](docs/guides/LangGraph使用补充_v0.2.1_20261013.md) 为准。先阅读 "), encoding="utf-8")
    files.extend([(notice, "README.md"), (repo / "README.md", "协作仓库历史说明.md")])
    source_manifest = {name: file_hash(path) for path, name in files}
    save(output / "source_manifest.json", source_manifest)
    files.append((output / "source_manifest.json", "source_manifest.json"))
    records = [zip_checked(output / f"灵伴LangGraph_开发包与使用文档_{label}.zip", files)]
    if demo:
        bundle = load(demo / "drafts/r1/review_bundle.json")
        verify_bundle(demo, bundle)
        selected = set(bundle["files"]) | set(bundle["media"]) | set(bundle.get("source_files", {})) | {"drafts/r1/review_bundle.json", "drafts/r1/decision.example.json"}
        items = [(demo / relative, relative) for relative in sorted(selected)]
        notice = output / "试听包说明.md"
        notice.write_text("# 统一时间轴兼容试听包\n\n解压后打开 drafts/r1/preview.html。本包复用已有真实配音与音效，验证程序排轴和混音，不是本轮新模型生成。人工听审及两个候选接触点待确认；未批准正式发布。音频、提示词、时间轴、源快照及哈希一并保留。\n", encoding="utf-8")
        items.append((notice, "试听包说明.md"))
        records.append(zip_checked(output / "统一时间轴_已有媒体草稿试听包_20261010.zip", items))
    save(output / "packages.json", {"packages": records, "credentials_included": False, "runtime_environment_included": False, "published_release": False})
    print(json.dumps(records, ensure_ascii=False))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--demo", type=Path)
    p.add_argument("--label", default="v0.2.1_20261013")
    args = p.parse_args()
    package(args.repo.resolve(), args.output.resolve(), args.demo.resolve() if args.demo else None, args.label)
