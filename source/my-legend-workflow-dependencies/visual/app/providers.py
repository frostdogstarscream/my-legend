from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from .config import Settings


# 网络层瞬断（网关提前关连接、限流、5xx）可安全重试；4xx 参数错误不重试。
RETRYABLE_HTTP_CODES = {408, 429, 500, 502, 503, 504}
MAX_ATTEMPTS = 3


class ProviderError(RuntimeError):
    pass


def _temperature(provider: str, model: str) -> float:
    # Kimi 部分推理模型只接受 temperature=1。
    if provider == "kimi_cn" or "kimi" in model.lower() or "k2" in model.lower():
        return 1.0
    return 0.8


def chat(
    system: str,
    user: str,
    *,
    settings: Settings | None = None,
    max_tokens: int = 12000,
    json_mode: bool = False,
) -> str:
    cfg = settings or Settings.load()
    if cfg.demo_mode:
        raise ProviderError("演示模式不调用外部模型")
    if not cfg.api_key:
        raise ProviderError("尚未配置 API Key")
    payload = {
        "model": cfg.model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": _temperature(cfg.provider, cfg.model),
        "max_tokens": max_tokens,
    }
    # DeepSeek、OpenAI 和通义的兼容接口均支持 JSON object mode。它能避免长篇
    # 剧本在模型端漏逗号、漏引号后被本地严格解析器拒绝。
    if json_mode and cfg.provider in {"deepseek", "openai", "qwen"}:
        payload["response_format"] = {"type": "json_object"}
    request = urllib.request.Request(
        cfg.base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {cfg.api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    data = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                data = json.loads(response.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code in RETRYABLE_HTTP_CODES and attempt < MAX_ATTEMPTS - 1:
                time.sleep(2 * (attempt + 1))
                continue
            raise ProviderError(f"模型接口返回 {exc.code}：{body[:800]}") from exc
        except Exception as exc:
            # 网关在高负载时会提前关闭长连接（如 IncompleteRead），重试通常即可恢复。
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(2 * (attempt + 1))
                continue
            raise ProviderError(f"模型连接失败：{exc}") from exc
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError("模型返回格式不符合 OpenAI 兼容协议") from exc


def test_connection(settings: Settings) -> str:
    reply = chat("你是连接测试助手。", "只回复：连接成功", settings=settings, max_tokens=20).strip()
    # 某些推理模型会把极短回答放入非 content 字段，HTTP 成功时 content 可能为空。
    return reply or "连接成功：模型接口已响应"
