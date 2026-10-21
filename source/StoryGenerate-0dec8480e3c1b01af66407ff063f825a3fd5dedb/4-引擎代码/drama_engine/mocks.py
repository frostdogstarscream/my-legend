"""非 LLM 能力的 Mock 实现。

它们存在的意义：让整张图在**无网络、无密钥、无 GPU** 的环境下端到端跑通。
生产替换时只需换实现，图与节点不动。
"""
from __future__ import annotations

from .contracts import RenderedAudio


class MockTTSRenderer:
    """按字符数估算时长。

    注意：这是**故意粗糙**的估算，用来暴露一个真实问题 ——
    文本长度估算在中文里失准（数字、专名、语气词的音节数差异极大），
    所以引擎必须支持回填 TTS 实测时长做二次校验，而不是信任字数。
    """

    name = "mock_tts"

    def __init__(self, chars_per_sec: float = 5.0) -> None:
        self.chars_per_sec = chars_per_sec

    def _duration(self, text: str, speech_rate: float = 1.0) -> float:
        cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
        return round(max(0.6, cjk / (self.chars_per_sec * max(0.5, speech_rate))), 2)

    def render_line(self, line: dict, character_card: dict | None) -> RenderedAudio:
        rate = float((character_card or {}).get("speech_rate", 1.0))
        return RenderedAudio(
            line_id=line.get("id", ""),
            duration_sec=self._duration(line.get("text", ""), rate),
            audio_uri=None,
            voice_id=(character_card or {}).get("name"),
        )

    def render_episode(self, episode: dict, cast: list[dict]) -> list[RenderedAudio]:
        by_name = {c.get("name"): c for c in cast}
        out: list[RenderedAudio] = []
        for beat in episode.get("beats", []):
            for line in beat.get("lines", []):
                card = by_name.get(line.get("speaker") or "")
                out.append(self.render_line(line, card))
        return out


class MockMarketRetriever:
    """离线榜单。字段刻意保留 source / collected_at，提醒下游口径不可混用。"""

    name = "mock_market"

    def __init__(self) -> None:
        self._rows = [
            {"title": "万妖图录传 第十二季", "platform": "红果短剧", "heat": 10200, "audio_fit": 28},
            {"title": "糯糯下山，师兄们都慌了 第二季", "platform": "红果短剧", "heat": 8800, "audio_fit": 72},
            {"title": "谁让这个文盲修仙的 第三季", "platform": "红果短剧", "heat": 7400, "audio_fit": 45},
            {"title": "大将军扛楼养活百万大军 第二季", "platform": "红果短剧", "heat": 6100, "audio_fit": 62},
            {"title": "三天后穿越古代，我贷款搬空商城！", "platform": "红果短剧", "heat": 5900, "audio_fit": 62},
            {"title": "好雨知时节", "platform": "红果短剧", "heat": 5200, "audio_fit": 95},
            {"title": "亲家换锁我收回婚房", "platform": "红果短剧", "heat": 4700, "audio_fit": 95},
        ]

    def hot_list(self, platform: str, limit: int = 20) -> list[dict]:
        rows = [r for r in self._rows if r["platform"] == platform] or list(self._rows)
        return [
            {**r, "source": "mock", "collected_at": "2026-09-11T00:00:00+08:00"}
            for r in rows[:limit]
        ]
