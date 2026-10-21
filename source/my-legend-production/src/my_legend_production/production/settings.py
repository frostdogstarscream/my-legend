"""Deployment settings; credentials remain in the service environment."""
from dataclasses import dataclass
import os
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ProductionSettings:
    dsn: str
    root: Path
    schema: str = "my_legend_production"
    story_url: str = "http://127.0.0.1:8897"
    public_media_base: str = "http://127.0.0.1:8895"

    @classmethod
    def environment(cls):
        return cls(dsn=os.environ["MY_LEGEND_PRODUCTION_DSN"],
                   root=Path(os.environ["MY_LEGEND_PRODUCTION_RUNTIME"]),
                   schema=os.environ.get("MY_LEGEND_PRODUCTION_SCHEMA", "my_legend_production"),
                   story_url=os.environ.get("MY_LEGEND_STORY_URL", "http://127.0.0.1:8897"),
                   public_media_base=os.environ.get("MY_LEGEND_MEDIA_BASE_URL", "http://127.0.0.1:8895"))

    def __post_init__(self):
        for value in (self.story_url, self.public_media_base):
            parsed = urlsplit(value)
            if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("INVALID_SERVICE_URL")
            if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost", "::1")):
                raise ValueError("SERVICE_REQUIRES_HTTPS")
