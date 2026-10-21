"""Provider-cost primitives.

These objects calculate supplier cost only.  Product pricing, credits and
subscriptions are intentionally separate concerns.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class UsageSource(str, Enum):
    REPORTED = "reported"
    ESTIMATED = "estimated"
    UNAVAILABLE = "unavailable"


class VersionedModelPrice(BaseModel):
    model_id: str
    price_revision: str
    currency: str = "USD"
    effective_from: datetime
    input_per_million: Decimal = Decimal("0")
    output_per_million: Decimal = Decimal("0")
    cache_read_per_million: Decimal = Decimal("0")
    cache_write_per_million: Decimal = Decimal("0")
    reasoning_billed_separately: bool = False
    reasoning_per_million: Decimal = Decimal("0")


class SupplierCostEstimate(BaseModel):
    currency: str
    price_revision: str
    confirmed_amount: Decimal = Decimal("0")
    estimated_amount: Decimal = Decimal("0")
    unknown_call_count: int = 0
    call_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    warnings: list[str] = Field(default_factory=list)


def calculate_supplier_cost(call: dict[str, Any], price: VersionedModelPrice) -> SupplierCostEstimate:
    """Calculate one call without double-counting included reasoning tokens."""
    source = UsageSource(call.get("usage_source", UsageSource.UNAVAILABLE))
    if source == UsageSource.UNAVAILABLE:
        return SupplierCostEstimate(
            currency=price.currency,
            price_revision=price.price_revision,
            unknown_call_count=1,
            call_count=1,
            warnings=["provider usage unavailable; supplier invoice reconciliation required"],
        )

    input_tokens = int(call.get("input_tokens") or 0)
    output_tokens = int(call.get("output_tokens") or 0)
    cache_read = int(call.get("cache_read_tokens") or 0)
    cache_write = int(call.get("cache_write_tokens") or 0)
    reasoning = int(call.get("reasoning_tokens") or 0)
    amount = (
        Decimal(input_tokens) * price.input_per_million
        + Decimal(output_tokens) * price.output_per_million
        + Decimal(cache_read) * price.cache_read_per_million
        + Decimal(cache_write) * price.cache_write_per_million
    ) / Decimal(1_000_000)
    if price.reasoning_billed_separately:
        amount += Decimal(reasoning) * price.reasoning_per_million / Decimal(1_000_000)
    target = "confirmed_amount" if source == UsageSource.REPORTED else "estimated_amount"
    return SupplierCostEstimate(
        currency=price.currency,
        price_revision=price.price_revision,
        **{target: amount},
        call_count=1,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        reasoning_tokens=reasoning,
        warnings=[] if source == UsageSource.REPORTED else ["local token estimate; not supplier billing evidence"],
    )


def summarize_supplier_cost(
    calls: list[dict[str, Any]], prices: dict[str, VersionedModelPrice],
) -> dict[str, Any]:
    """Aggregate attempts and preserve confirmed/estimated/unknown buckets."""
    totals: dict[str, Any] = {
        "confirmed_amount": Decimal("0"), "estimated_amount": Decimal("0"),
        "unknown_call_count": 0, "call_count": len(calls),
        "input_tokens": 0, "output_tokens": 0,
        "cache_read_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0,
        "unpriced_models": set(),
    }
    for call in calls:
        model = call.get("actual_model") or call.get("requested_model")
        price = prices.get(model)
        if price is None:
            totals["unknown_call_count"] += 1
            totals["unpriced_models"].add(model or "<unknown>")
            continue
        item = calculate_supplier_cost(call, price)
        for field in (
            "confirmed_amount", "estimated_amount", "unknown_call_count",
            "input_tokens", "output_tokens", "cache_read_tokens",
            "cache_write_tokens", "reasoning_tokens",
        ):
            totals[field] += getattr(item, field)
    totals["unpriced_models"] = sorted(totals["unpriced_models"])
    return totals
