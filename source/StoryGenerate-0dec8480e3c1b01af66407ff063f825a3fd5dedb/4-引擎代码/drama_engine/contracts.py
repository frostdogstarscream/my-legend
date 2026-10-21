"""预留接口层（Seam Layer）。

这是"可接入大模型、可预留接口"的落点。原则：

  1. 编排层（graph.py）只依赖 Protocol，不依赖任何具体厂商。
  2. 每个 Protocol 都提供 Mock 实现，保证无网络、无密钥时可端到端跑通。
  3. 绑定关系由 Runtime 容器在启动时装配，可来自代码、环境变量或配置文件。

替换实现即可切换到生产环境，节点与图结构不需要改动。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable

from pydantic import BaseModel

# ============================================================================
# 1) LLM 提供方 —— 唯一被节点直接依赖的外部能力
# ============================================================================


class LLMSpec(BaseModel):
    """一次调用的模型规格。模型分级路由的载体。"""

    role: str = "default"          # 节点用途，如 "gadget_design" / "episode_beats"
    model: str = "mock"
    tier: str = ""                  # 逻辑档位 FAST/BALANCED/STRONG/LONG
    temperature: float = 0.8
    max_tokens: int = 4096
    # 允许注入供应商侧参数（如 top_p、thinking 开关），编排层不感知其含义
    extra: dict[str, Any] = {}


class LLMResult(BaseModel):
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    cached: bool = False


@runtime_checkable
class LLMProvider(Protocol):
    """大模型接入点。

    接入任意 OpenAI 兼容供应商（DeepSeek / 通义 / Kimi / GLM / OpenAI / vLLM 本地部署）
    只需实现 OpenAICompatProvider；若要接非兼容协议（如某些私有网关），实现本 Protocol 即可。
    """

    name: str

    def complete(self, spec: LLMSpec, system: str, user: str) -> LLMResult:
        """自由文本补全。"""
        ...

    def complete_structured(
        self, spec: LLMSpec, system: str, user: str, schema: type[BaseModel]
    ) -> BaseModel:
        """结构化补全。

        这是整个引擎的地基：没有强类型输出，校验节点就无从下手。
        实现方需自行处理"模型返回非法 JSON → 带错误重问一次"的兜底。
        """
        ...


# ============================================================================
# 2) 语音合成 —— 广播剧的特有依赖，且反哺校验
# ============================================================================


class RenderedAudio(BaseModel):
    line_id: str
    duration_sec: float
    audio_uri: str | None = None
    voice_id: str | None = None


@runtime_checkable
class TTSRenderer(Protocol):
    """语音合成接入点。

    注意它不只是"把字变成声"——它返回**实测时长**，用于回填节拍表做二次校验。
    文本长度估算会在中文里失准（数字、专名、语气词差异极大），
    所以节拍落位的最终裁决权应交还给 TTS 的实测时长。
    """

    def render_line(self, line: dict, character_card: dict | None) -> RenderedAudio:
        ...

    def render_episode(self, episode: dict, cast: list[dict]) -> list[RenderedAudio]:
        ...


# ============================================================================
# 3) 市场数据检索 —— 选题节点的输入源
# ============================================================================


@runtime_checkable
class MarketRetriever(Protocol):
    """榜单 / 热点数据接入点（红果热播榜、DataEye 热力榜等）。

    提示：不同来源的热度口径不统一，实现方应保留 source 与 collected_at 字段，
    让下游知道这个数字的来路，避免把三套指标混在一张表里比较。
    """

    def hot_list(self, platform: str, limit: int = 20) -> list[dict]:
        ...


# ============================================================================
# 4) 版权守卫 —— 呼应"不整本复制他人作品"的合规前置
# ============================================================================


class SimilarityHit(BaseModel):
    reference: str
    score: float
    span: str


@runtime_checkable
class CopyrightGuard(Protocol):
    """生成文本 → 参考作品相似度检查。

    放在流水线里而不是事后人工审，是因为"抄了哪一部"在生成阶段最容易定位与改写。
    实现可以是向量检索 + n-gram 重合，也可以是外部查重服务。
    """

    def check(self, text: str) -> list[SimilarityHit]:
        ...


# ============================================================================
# 5) 遥测 —— 成本与质量的可观测性
# ============================================================================


class TokenUsage(BaseModel):
    node: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cached: bool = False


@runtime_checkable
class TelemetrySink(Protocol):
    """埋点接入点。生成 80 集是长任务，没有成本画像就无法做预算控制。"""

    def record(self, usage: TokenUsage) -> None:
        ...

    def event(self, name: str, payload: dict | None = None) -> None:
        ...


# ============================================================================
# 6) 运行时容器 —— 依赖装配
# ============================================================================


class NoopTelemetry:
    name = "noop"

    def record(self, usage: TokenUsage) -> None:  # noqa: D102
        pass

    def event(self, name: str, payload: dict | None = None) -> None:  # noqa: D102
        pass


class NoopCopyrightGuard:
    name = "noop"

    def check(self, text: str) -> list[SimilarityHit]:  # noqa: D102
        return []


class InMemoryTelemetry:
    """默认遥测：把用量累计在内存里，便于测试与报告。"""

    name = "in_memory"

    def __init__(self) -> None:
        self.usages: list[TokenUsage] = []
        self.events: list[tuple[str, dict]] = []

    def record(self, usage: TokenUsage) -> None:
        self.usages.append(usage)

    def event(self, name: str, payload: dict | None = None) -> None:
        self.events.append((name, payload or {}))

    def summary(self) -> dict:
        by_node: dict[str, dict] = {}
        for u in self.usages:
            slot = by_node.setdefault(
                u.node, {"calls": 0, "in": 0, "out": 0, "latency_ms": 0, "cached": 0}
            )
            slot["calls"] += 1
            slot["in"] += u.input_tokens
            slot["out"] += u.output_tokens
            slot["latency_ms"] += u.latency_ms
            slot["cached"] += 1 if u.cached else 0
        return {
            "total_calls": len(self.usages),
            "total_input_tokens": sum(u.input_tokens for u in self.usages),
            "total_output_tokens": sum(u.output_tokens for u in self.usages),
            "by_node": by_node,
        }


@dataclass
class Runtime:
    """依赖装配容器。由 CLI 或服务层构造，随 config 传入各节点。

    这是"预留接口"的可操作形态：新增一个外部能力 = 在这里加一个字段 + 一个 Protocol。
    """

    llm: LLMProvider
    tts: TTSRenderer | None = None
    market: MarketRetriever | None = None
    copyright_guard: CopyrightGuard = field(default_factory=NoopCopyrightGuard)
    telemetry: TelemetrySink = field(default_factory=InMemoryTelemetry)
    extras: dict[str, Any] = field(default_factory=dict)

    def has_tts(self) -> bool:
        return self.tts is not None

    def has_market(self) -> bool:
        return self.market is not None


# ---- 轻量服务定位器：让节点可以在不求助于全局单例的情况下取到 Runtime ----
_RUNTIME_FACTORY: Callable[[], Runtime] | None = None


def set_runtime_factory(factory: Callable[[], Runtime]) -> None:
    """服务层启动时调用一次，把 Runtime 的构造方式注入。"""
    global _RUNTIME_FACTORY
    _RUNTIME_FACTORY = factory


def get_runtime() -> Runtime:
    if _RUNTIME_FACTORY is None:
        raise RuntimeError("Runtime 未装配。请先调用 set_runtime_factory()。")
    return _RUNTIME_FACTORY()
