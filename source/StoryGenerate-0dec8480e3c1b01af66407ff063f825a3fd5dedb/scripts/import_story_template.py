"""Validate or import one versioned story template JSON file.

Validation is read-only by default. Use --install with DATABASE_URL to insert
an active, immutable-by-convention version into story_templates.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from drama_engine.templates.models import StoryTemplateSpec

ALLOWED_MODES = {"general", "mystery", "serialized", "viral_drama"}


def load_template(path: Path) -> StoryTemplateSpec:
    spec = StoryTemplateSpec.model_validate(json.loads(path.read_text(encoding="utf-8")))
    if not spec.template_id.strip() or not spec.name.strip():
        raise ValueError("template_id and name are required")
    if not spec.beats:
        raise ValueError("at least one beat is required")
    keys = [beat.key.strip() for beat in spec.beats]
    if any(not key for key in keys) or len(keys) != len(set(keys)):
        raise ValueError("beat keys must be nonempty and unique")
    unknown = set(spec.supported_modes) - ALLOWED_MODES
    if unknown:
        raise ValueError(f"unsupported modes: {sorted(unknown)}")
    return spec


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="UTF-8 JSON template file")
    parser.add_argument("--install", action="store_true", help="insert active version into PostgreSQL")
    args = parser.parse_args()
    spec = load_template(args.path)
    if args.install:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        from drama_engine.templates.postgres_repository import PostgresStoryTemplateRepository

        url = os.environ.get("DATABASE_URL")
        if not url:
            parser.error("DATABASE_URL is required for --install")
        engine = create_engine(url)
        try:
            with Session(engine) as session, session.begin():
                PostgresStoryTemplateRepository(session).create(spec)
        finally:
            engine.dispose()
    print(json.dumps({
        "template_id": spec.template_id,
        "version": spec.version,
        "beats": [beat.key for beat in spec.beats],
        "installed": args.install,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
