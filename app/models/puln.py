"""Typed view of the public ``live.json`` endpoint.

The observed JSON is an example, not a guaranteed contract: validation is strict about the
values we rely on and tolerant of fields we do not know yet (they are reported, not dropped
silently).
"""

import json
from datetime import datetime
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class LivePayloadError(ValueError):
    """The live endpoint returned data that cannot be trusted."""


class _LiveModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="allow", frozen=True)

    def known_fields(self) -> dict[str, Any]:
        """JSON-compatible dump without fields that are not part of the model."""
        return self.model_dump(mode="json", include=set(type(self).model_fields))


class LiveProduct(_LiveModel):
    id: int
    stock_remaining: int = Field(ge=0)
    stock_allocated: int | None = Field(default=None, ge=0)


class LiveSlot(_LiveModel):
    id: int
    remaining: int = Field(ge=0)
    label: str


class LiveStatus(_LiveModel):
    status: str
    accepting: bool
    opens_at: datetime | None = None
    closes_at: datetime | None = None
    server_now: datetime | None = None
    products: list[LiveProduct] = Field(default_factory=list)
    slots: list[LiveSlot] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        if len({p.id for p in self.products}) != len(self.products):
            raise ValueError("duplicate product id")
        if len({s.id for s in self.slots}) != len(self.slots):
            raise ValueError("duplicate slot id")
        return self

    def product(self, product_id: int) -> LiveProduct | None:
        return next((p for p in self.products if p.id == product_id), None)

    def slot(self, slot_id: int) -> LiveSlot | None:
        return next((s for s in self.slots if s.id == slot_id), None)

    def to_json(self) -> str:
        """Serialise only the known fields, suitable for storing the last valid state."""
        data = self.known_fields()
        data["products"] = [p.known_fields() for p in self.products]
        data["slots"] = [s.known_fields() for s in self.slots]
        return json.dumps(data, ensure_ascii=False)


def _describe(error: ValidationError) -> str:
    parts = []
    for item in error.errors(include_input=False, include_url=False):
        location = ".".join(str(part) for part in item["loc"]) or "payload"
        parts.append(f"{location}: {item['msg']}")
    return "; ".join(parts)


def parse_live_status(content: bytes) -> LiveStatus:
    """Validate a raw live.json body. Raises LivePayloadError without echoing the payload."""
    try:
        return LiveStatus.model_validate_json(content)
    except ValidationError as error:
        raise LivePayloadError(_describe(error)) from None


def unknown_fields(status: LiveStatus) -> set[str]:
    """Names of fields the endpoint sent that this model does not know (schema drift)."""
    found = set(status.model_extra or {})
    for product in status.products:
        found.update(f"products[].{name}" for name in product.model_extra or {})
    for slot in status.slots:
        found.update(f"slots[].{name}" for name in slot.model_extra or {})
    return found
