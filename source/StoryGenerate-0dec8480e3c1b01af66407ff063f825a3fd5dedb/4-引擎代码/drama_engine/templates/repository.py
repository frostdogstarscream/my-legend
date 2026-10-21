"""Phase 5: StoryTemplateRepository Protocol + Memory/Postgres 实现。

遵循 Repository Protocol 模式（与 persistence/repository.py 一致）。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import StoryTemplateSpec


# ============================================================================
# StoryTemplateRepository Protocol
# ============================================================================
@runtime_checkable
class StoryTemplateRepository(Protocol):
    """故事模板仓库接口。

    生产实现: PostgresStoryTemplateRepository
    测试实现: MemoryStoryTemplateRepository
    """

    def get(self, template_id: str, version: int | None = None) -> StoryTemplateSpec | None:
        """获取模板。version=None → 最新 active version。"""
        ...

    def create(self, spec: StoryTemplateSpec) -> StoryTemplateSpec:
        """新增模板。"""
        ...

    def list(self, mode: str | None = None) -> list[StoryTemplateSpec]:
        """列出模板。mode 过滤。"""
        ...


# ============================================================================
# Memory Story Template Repository
# ============================================================================
class MemoryStoryTemplateRepository:
    """内存实现。支持测试 + PostgreSQL 不可用的环境。"""

    def __init__(self) -> None:
        self._store: dict[str, StoryTemplateSpec] = {}

    def _key(self, template_id: str, version: int) -> str:
        return f"{template_id}:v{version}"

    def get(self, template_id: str, version: int | None = None) -> StoryTemplateSpec | None:
        if version is not None:
            return self._store.get(self._key(template_id, version))

        # 找最新版本
        best = None
        best_ver = 0
        for k, v in self._store.items():
            if k.startswith(f"{template_id}:v"):
                if v.version > best_ver:
                    best = v
                    best_ver = v.version
        return best

    def create(self, spec: StoryTemplateSpec) -> StoryTemplateSpec:
        k = self._key(spec.template_id, spec.version)
        if k in self._store:
            raise ValueError(
                f"模板 {spec.template_id} v{spec.version} 已存在。"
                f"请增加 version 或删除旧版。"
            )
        self._store[k] = spec
        return spec

    def list(self, mode: str | None = None) -> list[StoryTemplateSpec]:
        result = list(self._store.values())
        if mode:
            result = [t for t in result if mode in t.supported_modes]
        return sorted(result, key=lambda t: (t.template_id, -t.version))