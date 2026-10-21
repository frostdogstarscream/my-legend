"""Phase 4: PostgreSQL 持久化基础。

本模块实现以下 Repository：
    - CharacterTemplateRepository (memory + postgres)
    - StoryTemplateRepository  (memory + postgres)
    - StoryJobRepository       (memory + postgres)
    - StoryRecordRepository    (memory + postgres)
    - GenerationTraceRepository (memory + postgres)
    - QualityResultRepository  (memory + postgres)

原则：
    - 业务节点不直接依赖 SQLAlchemy / SQL / table
    - 所有 Repository 通过 Protocol 暴露
    - Memory 实现用于 dev / test / legacy regression
    - PostgreSQL 实现用于生产
    - 生产环境不可用时拒绝请求（不静默 fallback memory）
"""
from .settings import PersistenceSettings
from .database import get_engine, get_session, init_db
from .models import (
    Base,
    CharacterTemplateModel,
    StoryTemplateModel,
    StoryJobModel,
    StoryRecordModel,
    GenerationTraceModel,
    QualityResultModel,
)
from .repository import (
    build_repositories,
    PostgresCharacterTemplateRepository,
    PostgresStoryTemplateRepository,
    PostgresStoryJobRepository,
    PostgresStoryRecordRepository,
    PostgresGenerationTraceRepository,
    PostgresQualityResultRepository,
)

__all__ = [
    "PersistenceSettings",
    "get_engine",
    "get_session",
    "init_db",
    "Base",
    "CharacterTemplateModel",
    "StoryTemplateModel",
    "StoryJobModel",
    "StoryRecordModel",
    "GenerationTraceModel",
    "QualityResultModel",
    "build_repositories",
    "PostgresCharacterTemplateRepository",
    "PostgresStoryTemplateRepository",
    "PostgresStoryJobRepository",
    "PostgresStoryRecordRepository",
    "PostgresGenerationTraceRepository",
    "PostgresQualityResultRepository",
]