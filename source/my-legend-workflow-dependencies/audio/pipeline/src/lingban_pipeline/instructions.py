"""Local production policy for per-line delivery directions, not provider limits."""
from __future__ import annotations

import json

from .registry import CapabilityError


def weighted_length(text: str) -> int:
    def han(code: int):
        return (0x3400 <= code <= 0x4DBF or 0x4E00 <= code <= 0x9FFF
                or 0xF900 <= code <= 0xFAFF or 0x20000 <= code <= 0x323AF)
    return sum(2 if han(ord(char)) else 1 for char in text)


def policy(config: dict, performance: dict | None = None):
    configured = config.get("instruction_policy", {})
    if not isinstance(configured, dict) or set(configured) - {"max_weight", "max_corrections", "recommended_han_range"}:
        raise CapabilityError("Unknown instruction policy fields")
    result = {"max_weight": 240, "max_corrections": 2, "recommended_han_range": [50, 100]} | configured
    if performance and performance.get("instruction_weight_override") is not None:
        result["max_weight"] = performance["instruction_weight_override"]
        result["override"] = True
    for key in ("max_weight", "max_corrections"):
        value = result[key]
        if not isinstance(value, int) or isinstance(value, bool) or value < (1 if key == "max_weight" else 0):
            raise CapabilityError("Invalid instruction policy")
    if result["max_corrections"] > 2:
        raise CapabilityError("Instruction correction rounds cannot exceed two")
    advice = result["recommended_han_range"]
    if not isinstance(advice, list) or len(advice) != 2 or any(not isinstance(v, int) or isinstance(v, bool) or v < 1 for v in advice) or advice[0] > advice[1]:
        raise CapabilityError("Invalid recommended Han-character range")
    return result


class InstructionLengthError(ValueError):
    def __init__(self, candidate: str, maximum: int):
        self.weight = weighted_length(candidate)
        self.maximum = maximum
        self.repair_message = (
            f"The delivery instruction has weighted length {self.weight}; limit {maximum}; "
            f"remove at least {self.weight - maximum} weighted units. Han characters count 2, "
            "all other characters count 1. Revise the rejected candidate below instead of starting "
            "over. Preserve intent, emotional entry/turn/release/close, required emphasis and pauses; "
            "remove redundant modifiers first. Never rewrite spoken text. Return the same JSON contract. "
            + json.dumps({"rejected_instruction": candidate}, ensure_ascii=False)
        )
        super().__init__("instruction_weight_exceeded")


def validate_instruction(text: str, maximum: int):
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Invalid instructions")
    if weighted_length(text) > maximum:
        raise InstructionLengthError(text, maximum)
    return text


def adapter_instruction(text: str, config: dict, payload: dict):
    maximum = payload.get("instruction_max_weight", config.get("instruction_max_weight", 240))
    if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 1:
        raise CapabilityError("Invalid instruction weight limit")
    try:
        return validate_instruction(text, maximum)
    except InstructionLengthError as error:
        exc = CapabilityError("Delivery instruction exceeds configured production budget; compact before TTS")
        exc.diagnostics = {"category": "instruction_weight_exceeded", "weight": error.weight, "limit": maximum}
        raise exc from None
