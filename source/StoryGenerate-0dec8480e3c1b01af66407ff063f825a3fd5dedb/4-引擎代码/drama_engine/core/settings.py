"""应用环境配置 —— Task 1: APP_ENV + Prod Mock Guard。

原则：
  - dev/test: 允许 MockLLMProvider
  - prod: 缺少真实 Provider → 启动/请求失败，绝不自动回退 Mock
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum


class AppEnv(str, Enum):
    DEV = "dev"
    TEST = "test"
    PROD = "prod"


@dataclass
class Settings:
    """引擎配置。所有值均可通过环境变量覆盖。"""

    app_env: AppEnv = AppEnv.DEV

    # ---- 模型 Tier 配置 ----
    llm_fast_model: str = "qwen/qwen3.6-flash"
    llm_balanced_model: str = "qwen/qwen3.7-plus"
    llm_strong_model: str = "qwen/qwen3.7-max"
    llm_long_model: str = "zhipu/glm-5.3"
    llm_strong_alt_model: str = "qwen/deepseek-v4-pro"

    llm_api_key: str = ""
    llm_base_url: str = "https://moma.cmecloud.cn/v1"
    llm_timeout_sec: float = 180.0

    # ---- Deadline ----
    global_deadline_sec: float = 900.0
    node_default_timeout_sec: float = 120.0

    # ---- HTTP ----
    http_connect_timeout_sec: float = 15.0
    http_read_timeout_sec: float = 300.0
    http_write_timeout_sec: float = 30.0

    # ---- Retry ----
    max_retries: int = 1

    # ---- Repair ----
    max_total_repairs: int = 2
    max_repairs_per_stage: int = 1

    # ---- Output Guard ----
    min_content_chars: int = 50

    # ---- Per-node timeout overrides (seconds) ----
    node_timeouts: dict[str, float] = field(default_factory=lambda: {
        "topic_select": 60,
        "gadget_design": 90,
        "cast_design": 120,
        "outline": 90,
        "behavior_design": 180,
        "fact_ledger": 120,
        "episode_plan": 90,
        "episode_writer": 240,
        "episode_beats": 120,
        "judge": 90,
    })

    @property
    def is_prod(self) -> bool:
        return self.app_env == AppEnv.PROD

    @property
    def allow_mock(self) -> bool:
        """Mock 只允许在 dev/test 环境使用。"""
        return self.app_env in (AppEnv.DEV, AppEnv.TEST)


def get_settings() -> Settings:
    """从环境变量构造 Settings。

    环境变量映射:
      APP_ENV                  → app_env
      STORY_LLM_FAST_MODEL      → llm_fast_model
      STORY_LLM_BALANCED_MODEL  → llm_balanced_model
      STORY_LLM_STRONG_MODEL    → llm_strong_model
      STORY_LLM_LONG_MODEL      → llm_long_model
      STORY_LLM_STRONG_ALT_MODEL→ llm_strong_alt_model
      DRAMA_LLM_API_KEY / STORY_LLM_API_KEY → llm_api_key
      DRAMA_LLM_BASE_URL / STORY_LLM_BASE_URL → llm_base_url
      DRAMA_LLM_TIMEOUT        → llm_timeout_sec
      STORY_GLOBAL_DEADLINE_SEC → global_deadline_sec
      STORY_NODE_DEFAULT_TIMEOUT_SEC → node_default_timeout_sec
    """
    s = Settings()

    env = os.environ.get("APP_ENV", "").lower()
    if env in ("prod", "production"):
        s.app_env = AppEnv.PROD
    elif env in ("test", "testing"):
        s.app_env = AppEnv.TEST
    else:
        s.app_env = AppEnv.DEV

    # 模型
    for attr, env_var in (
        ("llm_fast_model", "STORY_LLM_FAST_MODEL"),
        ("llm_balanced_model", "STORY_LLM_BALANCED_MODEL"),
        ("llm_strong_model", "STORY_LLM_STRONG_MODEL"),
        ("llm_long_model", "STORY_LLM_LONG_MODEL"),
        ("llm_strong_alt_model", "STORY_LLM_STRONG_ALT_MODEL"),
    ):
        if os.environ.get(env_var):
            setattr(s, attr, os.environ[env_var])

    # API key (兼容两套命名)
    s.llm_api_key = (
        os.environ.get("DRAMA_LLM_API_KEY")
        or os.environ.get("STORY_LLM_API_KEY")
        or os.environ.get("OPENAI_API_KEY", "")
    )

    # Base URL
    s.llm_base_url = (
        os.environ.get("DRAMA_LLM_BASE_URL")
        or os.environ.get("STORY_LLM_BASE_URL")
        or s.llm_base_url
    )

    # Timeout
    if os.environ.get("DRAMA_LLM_TIMEOUT"):
        s.llm_timeout_sec = float(os.environ["DRAMA_LLM_TIMEOUT"])

    # Deadline
    if os.environ.get("STORY_GLOBAL_DEADLINE_SEC"):
        s.global_deadline_sec = float(os.environ["STORY_GLOBAL_DEADLINE_SEC"])
    if os.environ.get("STORY_NODE_DEFAULT_TIMEOUT_SEC"):
        s.node_default_timeout_sec = float(os.environ["STORY_NODE_DEFAULT_TIMEOUT_SEC"])

    return s


# 模块级缓存
_settings: Settings | None = None


def reload_settings() -> Settings:
    global _settings
    _settings = get_settings()
    return _settings
