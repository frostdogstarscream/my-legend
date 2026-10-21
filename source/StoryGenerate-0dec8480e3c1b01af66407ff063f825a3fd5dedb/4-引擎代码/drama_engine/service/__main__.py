"""python -m drama_engine.service {migrate,serve,worker,openapi}."""

import argparse
import json
import os
from pathlib import Path

from alembic.config import Config
from sqlalchemy import text

from alembic import command

from .config import ServiceSettings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["migrate", "serve", "worker", "openapi"])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    settings = ServiceSettings.from_env()
    settings.validate()
    if args.command == "serve":
        import uvicorn

        uvicorn.run(
            "drama_engine.service.api:create_app",
            factory=True,
            host=args.host,
            port=args.port,
        )
        return
    engine = settings.engine()
    if args.command == "migrate":
        root = Path(__file__).resolve().parents[3]
        config = Config(str(root / "alembic.ini"))
        config.set_main_option("script_location", str(root / "alembic"))
        if settings.schema:
            admin = __import__("sqlalchemy").create_engine(settings.database_url)
            with admin.begin() as c:
                c.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{settings.schema}"'))
            admin.dispose()
        with engine.begin() as c:
            config.attributes["connection"] = c
            command.upgrade(config, "head")
    elif args.command == "worker":
        from .worker import DurableWorker

        with engine.connect() as c:
            if (
                c.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                != "004"
            ):
                raise RuntimeError("SERVICE_MIGRATION_004_REQUIRED")
        DurableWorker(engine, settings).run()
    else:
        from .api import create_app

        document = create_app(settings, engine).openapi()
        output = args.output or Path("docs/phase11/openapi.json")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    engine.dispose()


if __name__ == "__main__":
    main()
