from __future__ import annotations

import io
import json
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import PROVIDERS, ROOT, Settings
from .db import add_version, create_project, delete_project, get_project, get_version, init_db, list_projects
from .generator import format_script, generate_script, generate_story_script, quality_report
from .providers import ProviderError, test_connection
from .schemas import ConfigUpdate, ConnectionTest, GenerateRequest, RewriteRequest, StoryGenerateRequest, StoryPreviewRequest
from .script_lines import compile_script_lines, validate_script_lines
from .story_input import StoryInputError, build_source_text, parse_story_document
from .tpm import compile_tpm, validate_tpm


app = FastAPI(title="入戏 · 小说转舞台剧", version="2.0.0")
STATIC = ROOT / "app" / "static"


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/api/v1/health")
def health() -> dict:
    return {"status": "ok", "version": app.version, "protocol": "TPM 1.0.0"}


@app.get("/api/v1/config")
def read_config() -> dict:
    return Settings.load().public()


@app.put("/api/v1/config")
def update_config(payload: ConfigUpdate) -> dict:
    old = Settings.load()
    key = old.api_key if payload.api_key.startswith("••••••") or not payload.api_key else payload.api_key
    settings = Settings(payload.demo_mode, payload.provider, payload.model, key, payload.base_url, old.database)
    settings.save()
    return settings.public()


@app.get("/api/v1/model-providers")
def model_providers() -> dict:
    return PROVIDERS


@app.post("/api/v1/model-providers/test")
def model_test(payload: ConnectionTest) -> dict:
    try:
        saved = Settings.load()
        api_key = saved.api_key if not payload.api_key or payload.api_key.startswith("••••••") else payload.api_key
        settings = Settings(False, payload.provider, payload.model, api_key, payload.base_url, saved.database)
        return {"ok": True, "message": test_connection(settings)}
    except ProviderError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/v1/projects")
def projects() -> list[dict]:
    return list_projects()


@app.get("/api/v1/projects/{project_id}")
def project(project_id: str) -> dict:
    result = get_project(project_id)
    if not result:
        raise HTTPException(404, "项目不存在")
    return result


@app.delete("/api/v1/projects/{project_id}")
def project_delete(project_id: str) -> dict:
    if not delete_project(project_id):
        raise HTTPException(404, "项目不存在")
    return {"ok": True}


@app.post("/api/v1/generate")
def generate(payload: GenerateRequest) -> dict:
    options = payload.options.model_dump()
    project_id = create_project(payload.title, payload.chapter_title, payload.source_text, options)
    try:
        script, source = generate_script(payload.title, payload.chapter_title, payload.source_text, options)
        script_text = format_script(script)
        tpm = compile_tpm(payload.title, script) if payload.options.enable_tpm else {}
        errors = validate_tpm(tpm) if tpm else []
        if errors:
            raise RuntimeError("TPM 校验失败：" + "；".join(errors))
        quality = quality_report(script, tpm, payload.options.duration_minutes)
        version_id = add_version(project_id, script_text, script, tpm, quality)
        return {"project_id": project_id, "version_id": version_id, "source": source, "source_text": payload.source_text, "script_text": script_text, "script": script, "tpm": tpm, "script_lines": compile_script_lines(payload.title, script), "quality": quality}
    except Exception as exc:
        delete_project(project_id)
        logging.exception("文本生成失败（project_id=%s）", project_id)
        raise HTTPException(502, str(exc)) from exc


@app.post("/api/v1/story-imports/preview")
def story_preview(payload: StoryPreviewRequest) -> dict:
    try:
        return parse_story_document(payload.document, payload.selected_chapters).public_preview()
    except StoryInputError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/v1/generate-from-story-json")
def generate_from_story_json(payload: StoryGenerateRequest) -> dict:
    try:
        context = parse_story_document(payload.document, payload.selected_chapters)
    except StoryInputError as exc:
        raise HTTPException(422, str(exc)) from exc
    if context.warnings and not payload.acknowledged_warnings:
        raise HTTPException(409, {"message": "该故事带有上游质量或降级警告，请确认后再生成。", "warnings": context.warnings})
    options = payload.options.model_dump()
    chapter_title = "、".join(chapter["title"] for chapter in context.chapters)
    source_text = build_source_text(context)
    project_id = create_project(context.title, chapter_title, source_text, options | {"input_type": "story_json", "source_story_id": context.story_id, "source_hash": context.source_hash})
    try:
        script, source = generate_story_script(context, options)
        script_text = format_script(script)
        tpm = compile_tpm(context.title, script) if payload.options.enable_tpm else {}
        errors = validate_tpm(tpm) if tpm else []
        if errors:
            raise RuntimeError("TPM 校验失败：" + "；".join(errors))
        quality = quality_report(script, tpm, payload.options.duration_minutes)
        quality["source_warnings"] = context.warnings
        version_id = add_version(project_id, script_text, script, tpm, quality)
        return {"project_id": project_id, "version_id": version_id, "source": source, "source_text": source_text, "script_text": script_text, "script": script, "tpm": tpm, "script_lines": compile_script_lines(context.title, script), "quality": quality, "source_preview": context.public_preview()}
    except Exception as exc:
        delete_project(project_id)
        logging.exception("故事 JSON 生成失败（project_id=%s）", project_id)
        raise HTTPException(502, str(exc)) from exc


@app.post("/api/v1/rewrite")
def rewrite(payload: RewriteRequest) -> dict:
    instructions = {
        "natural": "让表达更口语、更有来回回应",
        "stronger_conflict": "强化双方目标冲突和打断",
        "subtext": "增加潜台词，不直接说破真实目的",
        "actions": "加入简短、可执行的神态和动作",
        "shorter": "压缩重复信息，保留关键行动",
    }
    cfg = Settings.load()
    if cfg.demo_mode or not cfg.api_key:
        return {"candidate": f"【调整建议：{instructions[payload.mode]}】\n{payload.text}"}
    from .providers import chat
    try:
        candidate = chat("你是舞台剧对白编辑。只输出修改后的内容。", f"要求：{instructions[payload.mode]}\n原文：{payload.text}", settings=cfg, max_tokens=2000)
        return {"candidate": candidate}
    except ProviderError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.get("/api/v1/versions/{version_id}/export")
def export(version_id: str, format: str = "txt"):
    version = get_version(version_id)
    if not version:
        raise HTTPException(404, "版本不存在")
    if format == "txt":
        return PlainTextResponse(version["script_text"], headers={"Content-Disposition": f'attachment; filename="script-{version_id}.txt"'})
    if format == "tpm":
        return JSONResponse(version["tpm"], headers={"Content-Disposition": f'attachment; filename="performance-{version_id}.tpm.json"'})
    if format == "lines":
        document = compile_script_lines(version["script"].get("title", "舞台剧"), version["script"])
        errors = validate_script_lines(document)
        if errors:
            raise HTTPException(500, "script_lines 校验失败：" + "；".join(errors))
        return JSONResponse(document, headers={"Content-Disposition": f'attachment; filename="script-lines-{version_id}.json"'})
    if format == "docx":
        try:
            from docx import Document
        except ImportError as exc:
            raise HTTPException(501, "请安装 python-docx") from exc
        document = Document()
        for line in version["script_text"].splitlines():
            document.add_paragraph(line)
        buffer = io.BytesIO()
        document.save(buffer)
        buffer.seek(0)
        return StreamingResponse(buffer, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", headers={"Content-Disposition": f'attachment; filename="script-{version_id}.docx"'})
    raise HTTPException(400, "format 仅支持 txt、docx、tpm、lines")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
