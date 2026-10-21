"""Phase 5: Story Template Resolver — 确定性模板解析，0 LLM。

Resolution Priority:
  1. explicit template_id + version → 精确加载
  2. explicit template_id → 最新 active version
  3. mode default template
  4. freeform (无模板)
"""
from __future__ import annotations

from .models import ResolvedStoryTemplate, StoryTemplateSpec, TemplateSource
from .repository import MemoryStoryTemplateRepository, StoryTemplateRepository
from ..core.errors import EngineErrorCode, StoryEngineError


class TemplateNotFoundError(StoryEngineError):
    def __init__(self, template_id: str, version: int | None = None) -> None:
        ver_str = f" v{version}" if version else ""
        super().__init__(
            code=EngineErrorCode.STORY_TEMPLATE_NOT_FOUND,
            message=f"模板 '{template_id}'{ver_str} 不存在。"
        )


class TemplateVersionNotFoundError(StoryEngineError):
    def __init__(self, template_id: str, version: int) -> None:
        super().__init__(
            code=EngineErrorCode.STORY_TEMPLATE_VERSION_NOT_FOUND,
            message=f"模板 '{template_id}' v{version} 不存在。"
        )


class TemplateModeIncompatibleError(StoryEngineError):
    def __init__(self, template_id: str, requested_mode: str, supported: list[str]) -> None:
        super().__init__(
            code=EngineErrorCode.STORY_TEMPLATE_MODE_INCOMPATIBLE,
            message=f"模板 '{template_id}' 不支持模式 '{requested_mode}'。"
                    f" 支持: {', '.join(supported)}"
        )


class StoryTemplateResolver:
    """解析 Story Template，纯代码，0 LLM。

    使用方式:
        resolver = StoryTemplateResolver(repo, mode_defaults)
        resolved = resolver.resolve(
            template_id="GENERAL_THREE_ACT",
            version=None,
            mode_key="general"
        )
    """

    def __init__(
        self,
        repository: StoryTemplateRepository,
        mode_defaults: dict[str, str] | None = None,
    ) -> None:
        self._repo = repository
        self._mode_defaults: dict[str, str] = mode_defaults or {}

    def resolve(
        self,
        template_id: str | None = None,
        version: int | None = None,
        mode_key: str = "viral_drama",
    ) -> ResolvedStoryTemplate | None:
        """解析模板。返回 None 表示 freeform（该模式允许无模板）。"""

        # Priority 1: explicit template_id + version
        if template_id and version is not None:
            spec = self._repo.get(template_id, version)
            if spec is None:
                raise TemplateVersionNotFoundError(template_id, version)
            self._check_compatibility(spec, mode_key)
            return ResolvedStoryTemplate.from_spec(spec, TemplateSource.explicit)

        # Priority 2: explicit template_id → latest version
        if template_id:
            spec = self._repo.get(template_id, version=None)
            if spec is None:
                raise TemplateNotFoundError(template_id)
            self._check_compatibility(spec, mode_key)
            return ResolvedStoryTemplate.from_spec(spec, TemplateSource.explicit)

        # Priority 3: mode default template
        default_id = self._mode_defaults.get(mode_key)
        if default_id:
            spec = self._repo.get(default_id, version=None)
            if spec is not None:
                return ResolvedStoryTemplate.from_spec(spec, TemplateSource.mode_default)

        # Priority 4: freeform
        return None

    def resolve_or_default(
        self,
        template_id: str | None = None,
        version: int | None = None,
        mode_key: str = "viral_drama",
        default_spec: StoryTemplateSpec | None = None,
    ) -> ResolvedStoryTemplate:
        """resolve + builtin fallback。如果仓库无模板，使用 default_spec。"""
        try:
            result = self.resolve(template_id, version, mode_key)
            if result is not None:
                return result
        except StoryEngineError:
            if template_id is not None:
                raise  # 显式指定时仍然报错

        # freeform → 用 builtin default
        if default_spec:
            return ResolvedStoryTemplate.from_spec(default_spec, TemplateSource.mode_default)
        return ResolvedStoryTemplate(
            template_id="freeform",
            version=1,
            name="Freeform",
            source=TemplateSource.freeform,
        )

    @staticmethod
    def _check_compatibility(spec: StoryTemplateSpec, mode_key: str) -> None:
        if not spec.supported_modes:
            return  # 空列表 = 所有模式通用
        if mode_key not in spec.supported_modes:
            raise TemplateModeIncompatibleError(
                spec.template_id, mode_key, spec.supported_modes
            )


def build_template_resolver(
    repository: StoryTemplateRepository | None = None,
    mode_defaults: dict[str, str] | None = None,
) -> StoryTemplateResolver:
    """工厂函数：构建 Template Resolver。

    若 repository=None，使用 MemoryStoryTemplateRepository。
    """
    repo = repository or MemoryStoryTemplateRepository()
    return StoryTemplateResolver(repo, mode_defaults)