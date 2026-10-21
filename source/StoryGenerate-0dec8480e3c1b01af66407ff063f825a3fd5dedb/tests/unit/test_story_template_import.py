"""Versioned JSON template validation and SQLAlchemy persistence."""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from drama_engine.persistence.models import Base
from drama_engine.templates.postgres_repository import PostgresStoryTemplateRepository

from scripts.import_story_template import load_template


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("name", [
    "spy_mystery_closed.v1.json", "spy_mystery_serialized.v1.json",
])
def test_template_examples_validate_and_roundtrip(tmp_path, name):
    spec = load_template(ROOT / "templates" / name)
    engine = create_engine(f"sqlite:///{tmp_path / 'templates.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        repo = PostgresStoryTemplateRepository(session)
        repo.create(spec)
        session.commit()
        assert repo.get(spec.template_id, spec.version) == spec
        assert repo.get(spec.template_id).version == 1
        assert spec in repo.list(spec.supported_modes[0])
        with pytest.raises(ValueError, match="已存在"):
            repo.create(spec)
    engine.dispose()
