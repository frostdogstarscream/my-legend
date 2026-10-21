"""Phase 5: PostgresStoryTemplateRepository。

复用 Phase 4.1 persistence 体系（SQLAlchemy, Session）。
与 MemoryStoryTemplateRepository 实现相同 Protocol。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..persistence.models import StoryTemplateModel
from .models import StoryTemplateSpec
from .repository import StoryTemplateRepository


class PostgresStoryTemplateRepository:
    """PostgreSQL 实现的模板仓库。

    使用 Phase 4 的 SQLAlchemy Session（与 character_templates 同实例）。
    物理 PG 不可用时，使用 MemoryStoryTemplateRepository。
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, template_id: str, version: int | None = None) -> StoryTemplateSpec | None:
        query = self._session.query(StoryTemplateModel).filter_by(
            template_id=template_id, status="active",
        )
        if version is not None:
            query = query.filter_by(version=version)
        row = query.order_by(StoryTemplateModel.version.desc()).first()
        return StoryTemplateSpec.model_validate(row.template_json) if row else None

    def create(self, spec: StoryTemplateSpec) -> StoryTemplateSpec:
        existing = self._session.query(StoryTemplateModel).filter_by(
            template_id=spec.template_id, version=spec.version,
        ).first()
        if existing:
            raise ValueError(
                f"模板 {spec.template_id} v{spec.version} 已存在。"
                f"请增加 version 或删除旧版。"
            )
        self._session.add(StoryTemplateModel(
            template_id=spec.template_id,
            version=spec.version,
            status="active",
            name=spec.name,
            supported_modes=spec.supported_modes,
            supported_forms=["audio_drama"],
            tags=spec.tone_hints,
            template_json=spec.model_dump(mode="json"),
        ))
        self._session.flush()
        return spec

    def list(self, mode: str | None = None) -> list[StoryTemplateSpec]:
        results = self._session.query(StoryTemplateModel).filter_by(status="active").order_by(
            StoryTemplateModel.template_id,
            StoryTemplateModel.version.desc(),
        ).all()
        specs = [StoryTemplateSpec.model_validate(row.template_json) for row in results]
        if mode:
            specs = [t for t in specs if mode in t.supported_modes]
        return specs


def build_postgres_template_repo(session: Session) -> PostgresStoryTemplateRepository:
    """工厂：构建 PostgresStoryTemplateRepository。"""
    return PostgresStoryTemplateRepository(session)
