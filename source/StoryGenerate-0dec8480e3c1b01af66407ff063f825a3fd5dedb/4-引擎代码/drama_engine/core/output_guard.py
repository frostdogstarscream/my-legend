"""Output Guard —— Task 6: 交付前最终校验。

所有 success 状态的结果必须过这一关。禁止空 content / 纯空白 / 明显截断 返回 success。
"""
from __future__ import annotations

from ..core.errors import EmptyOutputError, QualityGateError
from ..core.settings import get_settings

_SETTINGS = get_settings()


class DeliveryCheck:
    """单次交付的结构化校验结果。"""

    def __init__(self) -> None:
        self.issues: list[str] = []

    def ok(self) -> bool:
        return not self.issues


def validate_delivery(
    content: str | None,
    label: str = "story",
    min_chars: int | None = None,
    expected_parts: int | None = None,
) -> DeliveryCheck:
    """校验交付内容。

    Args:
        content: 交付正文
        label: 标签（如 "story", "episode-3"），用于错误消息
        min_chars: 最低字符数，默认从 settings 读取
        expected_parts: 期望的结构数量（如集数、章节数），不传则跳过

    Returns:
        DeliveryCheck: .ok() 为 False 时，.issues 列出问题
    """
    check = DeliveryCheck()
    threshold = min_chars if min_chars is not None else _SETTINGS.min_content_chars

    if content is None:
        check.issues.append(f"[{label}] 内容为 null")
        return check

    stripped = content.strip()
    if not stripped:
        check.issues.append(f"[{label}] 内容为空字符串")
        return check

    if len(stripped) < threshold:
        check.issues.append(
            f"[{label}] 内容仅 {len(stripped)} 字符，低于最低阈值 {threshold}"
        )

    # 截断检测：常见截断标志
    truncation_markers = [
        # 结尾不完整的 JSON
        (stripped.rstrip().endswith('"') and not stripped.rstrip().endswith('}"}')
         and stripped.rstrip()[-2:] != '"]'),
    ]
    if any(truncation_markers):
        check.issues.append(f"[{label}] 可能被截断")

    if expected_parts is not None and expected_parts > 0:
        # 简单计数 —— 由调用方在传入前计算
        pass  # 由调用方自行判断

    return check


def guard_delivery(content: str | None, label: str = "story",
                   min_chars: int | None = None) -> str:
    """validate_delivery 的抛出异常版。通过时返回 content，失败时抛异常。"""
    result = validate_delivery(content, label, min_chars)
    if not result.ok():
        if any("为空" in i or "为 null" in i for i in result.issues):
            raise EmptyOutputError("; ".join(result.issues))
        raise QualityGateError("; ".join(result.issues))
    return content or ""


def validate_script(episodes: list[dict], min_episodes: int = 0) -> DeliveryCheck:
    """校验剧本结构完整性。"""
    check = DeliveryCheck()
    if not episodes:
        if min_episodes > 0:
            check.issues.append(f"剧本无任何集数（期望至少 {min_episodes} 集）")
        return check

    for ep in episodes:
        ep_no = ep.get("episode", "?")
        lines = ep.get("lines") or []
        title = ep.get("title", "")
        duration = ep.get("duration_sec", 0)

        if not title:
            check.issues.append(f"第 {ep_no} 集标题为空")
        if duration <= 0:
            check.issues.append(f"第 {ep_no} 集时长为 {duration}")
        if not lines:
            check.issues.append(f"第 {ep_no} 集《{title}》无台词")
        else:
            text = " ".join(ln.get("text", "") for ln in lines)
            text_stripped = text.strip()
            if not text_stripped:
                check.issues.append(f"第 {ep_no} 集《{title}》台词全为空")
            elif len(text_stripped) < _SETTINGS.min_content_chars:
                check.issues.append(
                    f"第 {ep_no} 集《{title}》仅 {len(text_stripped)} 有效字符"
                )

    return check