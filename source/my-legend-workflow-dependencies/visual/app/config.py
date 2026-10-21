from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"


def _read_env() -> dict[str, str]:
    values: dict[str, str] = {}
    if ENV_FILE.exists():
        for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


@dataclass
class Settings:
    demo_mode: bool = True
    provider: str = "deepseek"
    model: str = "deepseek-chat"
    api_key: str = ""
    base_url: str = "https://api.deepseek.com/v1"
    database: str = "data/ruxi.db"

    @classmethod
    def load(cls) -> "Settings":
        values = _read_env()
        get = lambda key, default: os.getenv(key, values.get(key, default))
        return cls(
            demo_mode=get("RUXI_DEMO_MODE", "true").lower() == "true",
            provider=get("RUXI_PROVIDER", "deepseek"),
            model=get("RUXI_MODEL", "deepseek-chat"),
            api_key=get("RUXI_API_KEY", ""),
            base_url=get("RUXI_BASE_URL", "https://api.deepseek.com/v1").rstrip("/"),
            database=get("RUXI_DATABASE", "data/ruxi.db"),
        )

    def public(self) -> dict:
        data = asdict(self)
        data["api_key"] = "" if not self.api_key else f"••••••{self.api_key[-4:]}"
        data["has_api_key"] = bool(self.api_key)
        return data

    def save(self) -> None:
        lines = [
            f"RUXI_DEMO_MODE={'true' if self.demo_mode else 'false'}",
            f"RUXI_PROVIDER={self.provider}",
            f"RUXI_MODEL={self.model}",
            f"RUXI_API_KEY={self.api_key}",
            f"RUXI_BASE_URL={self.base_url}",
            f"RUXI_DATABASE={self.database}",
        ]
        ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


PROVIDERS = {
    "openai": {"label": "OpenAI", "base_url": "https://api.openai.com/v1", "model": "gpt-5-mini"},
    "deepseek": {"label": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat"},
    "kimi_cn": {"label": "Kimi（中国大陆）", "base_url": "https://api.moonshot.cn/v1", "model": "kimi-k2.5"},
    "qwen": {"label": "通义千问", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus"},
    "custom": {"label": "自定义 OpenAI 兼容接口", "base_url": "", "model": ""},
}

