from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class GenerationOptions(BaseModel):
    duration_minutes: int = Field(default=10, ge=3, le=30)
    actor_limit: int | None = Field(default=None, ge=2, le=12)
    style: Literal["balanced", "realistic", "conflict", "suspense", "comedy"] = "balanced"
    detail_density: Literal["concise", "balanced", "rich"] = "rich"
    stage: Literal["campus", "blackbox", "small_theatre"] = "small_theatre"
    language_mode: Literal["auto", "modern", "classical_to_modern", "retain_classical"] = "auto"
    enable_tpm: bool = True


class GenerateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    chapter_title: str = Field(default="", max_length=160)
    source_text: str = Field(min_length=20, max_length=20000)
    options: GenerationOptions = Field(default_factory=GenerationOptions)


class StoryPreviewRequest(BaseModel):
    document: dict
    selected_chapters: list[int] = Field(default_factory=list)


class StoryGenerateRequest(StoryPreviewRequest):
    options: GenerationOptions = Field(default_factory=GenerationOptions)
    acknowledged_warnings: bool = False


class ConfigUpdate(BaseModel):
    provider: str
    model: str
    api_key: str = ""
    base_url: str
    demo_mode: bool = False


class ConnectionTest(BaseModel):
    provider: str
    model: str
    api_key: str
    base_url: str


class RewriteRequest(BaseModel):
    text: str = Field(min_length=1)
    mode: Literal["natural", "stronger_conflict", "subtext", "actions", "shorter"] = "natural"
