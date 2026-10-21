from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url


@dataclass
class ServiceSettings:
    database_url: str = field(repr=False)
    api_keys: dict[str, dict] = field(default_factory=dict, repr=False)
    schema: str | None = None
    lease_seconds: int = 120
    heartbeat_seconds: float = 10
    poll_seconds: float = 1
    testing: bool = False

    @classmethod
    def from_env(cls):
        return cls(
            database_url=os.environ.get("STORY_DATABASE_URL", ""),
            api_keys=json.loads(os.environ.get("STORY_API_KEYS", "{}")),
            schema=os.environ.get("STORY_DATABASE_SCHEMA") or None,
            lease_seconds=int(os.environ.get("STORY_WORKER_LEASE_SECONDS", "120")),
            heartbeat_seconds=float(
                os.environ.get("STORY_WORKER_HEARTBEAT_SECONDS", "10")
            ),
            poll_seconds=float(os.environ.get("STORY_WORKER_POLL_SECONDS", "1")),
        )

    def validate(self):
        if not self.database_url or (
            not self.testing
            and make_url(self.database_url).get_backend_name() != "postgresql"
        ):
            raise ValueError("SERVICE_REQUIRES_POSTGRESQL")
        if not self.api_keys or any(
            not isinstance(key, str)
            or not key
            or not isinstance(value, dict)
            or not isinstance(value.get("principal_id"), str)
            or not value["principal_id"]
            or len(value["principal_id"]) > 128
            or not isinstance(value.get("admin", False), bool)
            for key, value in self.api_keys.items()
        ):
            raise ValueError("SERVICE_AUTH_CONFIGURATION_REQUIRED")
        if self.schema and not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]{0,62}", self.schema
        ):
            raise ValueError("INVALID_DATABASE_SCHEMA")
        if (
            self.lease_seconds < 2
            or not 0 < self.heartbeat_seconds < self.lease_seconds / 2
            or self.poll_seconds <= 0
        ):
            raise ValueError("INVALID_WORKER_TIMING")

    def engine(self):
        self.validate()
        args = {"options": f"-csearch_path={self.schema}"} if self.schema else {}
        return create_engine(self.database_url, connect_args=args, pool_pre_ping=True)


def redact(value):
    """Keep evidence body intact except explicitly sensitive data."""
    secret_values = [
        os.environ.get(k, "")
        for k in (
            "DRAMA_LLM_API_KEY",
            "STORY_LLM_API_KEY",
            "STORY_API_KEYS",
            "STORY_DATABASE_URL",
        )
    ]
    try:
        secret_values += list(json.loads(os.environ.get("STORY_API_KEYS", "{}")))
    except (ValueError, TypeError):
        pass
    if isinstance(value, dict):
        return {
            k: (
                "[REDACTED]"
                if k.lower()
                in ("authorization", "api_key", "token", "password", "database_url")
                else redact(v)
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        for secret in secret_values:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        value = re.sub(
            r"(postgres(?:ql)?(?:\+\w+)?://[^:\s/]+:)[^@\s]+@", r"\1[REDACTED]@", value
        )
    return value
