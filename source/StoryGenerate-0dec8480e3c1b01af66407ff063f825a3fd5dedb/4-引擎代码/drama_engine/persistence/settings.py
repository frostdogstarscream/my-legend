"""Phase 4 数据库配置。

从环境变量 / Settings 读取持久化参数。
PERSISTENCE_BACKEND=memory 用于 dev/test/legacy regression。
PERSISTENCE_BACKEND=postgres 用于生产。
"""
from __future__ import annotations

import os
import urllib.parse
from dataclasses import dataclass, field


@dataclass
class PersistenceSettings:
    backend: str = "memory"  # "memory" | "postgres"
    database_url: str = ""
    pool_size: int = 5
    max_overflow: int = 10
    connect_timeout: int = 10

    @property
    def safe_url_for_log(self) -> str:
        """返回对日志安全的 DATABASE_URL（隐藏密码）。"""
        if not self.database_url:
            return "(empty)"
        try:
            parsed = urllib.parse.urlparse(self.database_url)
            if parsed.password:
                safe = parsed._replace(
                    netloc=f"{parsed.username}:****@{parsed.hostname}:{parsed.port or 5432}"
                )
                return safe.geturl()
            return self.database_url
        except Exception:
            return "(parse error)"

    @classmethod
    def from_env(cls) -> PersistenceSettings:
        """从环境变量读取（遵循 12-Factor App）。"""
        return cls(
            backend=os.environ.get("PERSISTENCE_BACKEND", "memory"),
            database_url=os.environ.get("DATABASE_URL", ""),
            pool_size=int(os.environ.get("DB_POOL_SIZE", "5")),
            max_overflow=int(os.environ.get("DB_MAX_OVERFLOW", "10")),
            connect_timeout=int(os.environ.get("DB_CONNECT_TIMEOUT", "10")),
        )