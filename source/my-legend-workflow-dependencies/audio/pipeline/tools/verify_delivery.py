"""Verify an existing draft bundle and loopback HTTP assets without provider calls."""
from __future__ import annotations

import argparse
import json
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import urlopen

import soundfile as sf

from lingban_pipeline.delivery import verify_bundle
from lingban_pipeline.io import file_hash, load, native, save, under


class References(HTMLParser):
    def __init__(self):
        super().__init__()
        self.audio, self.links = [], []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "audio" and attrs.get("src"):
            self.audio.append(attrs["src"])
        if tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])


def verify(root: Path, preview_url: str | None, revision: int = 1):
    root = root.resolve()
    directory = root / "drafts" / f"r{revision}"
    bundle = load(directory / "review_bundle.json")
    verify_bundle(root, bundle)
    parser = References()
    parser.feed((directory / "preview.html").read_text(encoding="utf-8"))
    checks = load(directory / "technical_checks.json")
    info = sf.info(native(directory / "draft.wav"))
    assert info.frames == checks["mix"]["frames"]
    assert file_hash(directory / "draft.wav") == bundle["mix_sha256"]
    media = []
    for relative in parser.audio:
        p = under(root, (directory / relative).resolve().relative_to(root).as_posix())
        actual = sf.info(native(p))
        media.append({"file": p.relative_to(root).as_posix(), "frames": actual.frames, "sample_rate": actual.samplerate, "sha256": file_hash(p)})
    http = []
    if preview_url:
        parsed = urlparse(preview_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise ValueError("Only the task's loopback preview may be checked")
        for url in [preview_url] + [urljoin(preview_url, rel) for rel in parser.audio + parser.links]:
            with urlopen(url, timeout=15) as response:
                assert response.status == 200
                assert response.read(64)
                http.append({"path": urlparse(url).path, "status": response.status})
    assets = load(directory / "assets.json")
    return {"bundle_verified": True, "bundle_sha256": bundle["bundle_sha256"], "asset_count": len(assets), "origin_counts": {origin: sum(a["raw"].get("origin") == origin for a in assets.values()) for origin in {a["raw"].get("origin") for a in assets.values()}}, "timeline_version": bundle["timeline_version"], "duration_ms": info.duration * 1000, "frames": info.frames, "sample_rate": info.samplerate, "channels": info.channels, "mix_sha256": bundle["mix_sha256"], "audio_references": len(parser.audio), "decoded_media": media, "http_checks": http, "technical_passed": checks["technical_passed"], "human_acceptance": checks["human_acceptance"], "visual_validation": checks["visual_validation"], "required_point_confirmations": bundle["required_point_confirmations"], "browser_interaction_verified": False, "new_provider_requests": 0}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--url")
    p.add_argument("--revision", type=int, default=1)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    result = verify(args.root, args.url, args.revision)
    save(args.output, result)
    print(json.dumps({k: result[k] for k in ("bundle_verified", "asset_count", "audio_references", "duration_ms", "technical_passed", "human_acceptance", "browser_interaction_verified")} | {"http_checked": len(result["http_checks"])}, ensure_ascii=False))
