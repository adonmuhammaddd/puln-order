"""SQLAlchemy tables. Records here are local tracking data, never proof of a PULN reservation."""

from datetime import datetime

from sqlalchemy import Enum, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.clock import utc_now
from app.database.base import Base
from app.models.booking import BookingStatus


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    display_name: Mapped[str] = mapped_column(String(120))
    whatsapp: Mapped[str] = mapped_column(String(40))
    email: Mapped[str] = mapped_column(String(255))
    consent_confirmed: Mapped[bool] = mapped_column(default=False)
    is_active: Mapped[bool] = mapped_column(default=True)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, onupdate=utc_now)


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    puln_product_id: Mapped[int] = mapped_column(unique=True)
    name: Mapped[str] = mapped_column(String(160))
    max_quantity: Mapped[int | None]
    is_target: Mapped[bool] = mapped_column(default=False)
    is_active: Mapped[bool] = mapped_column(default=True)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, onupdate=utc_now)


class PickupSlot(Base):
    """Latest known state of a slot. PULN slot IDs are scoped to their session."""

    __tablename__ = "pickup_slots"
    __table_args__ = (UniqueConstraint("session_slug", "puln_slot_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    puln_slot_id: Mapped[int]
    session_slug: Mapped[str] = mapped_column(String(80))
    label: Mapped[str] = mapped_column(String(255))
    last_remaining: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, onupdate=utc_now)


class StockSnapshot(Base):
    __tablename__ = "stock_snapshots"
    __table_args__ = (
        Index("ix_stock_snapshots_lookup", "session_slug", "puln_product_id", "observed_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_slug: Mapped[str] = mapped_column(String(80))
    puln_product_id: Mapped[int]
    stock_remaining: Mapped[int]
    stock_allocated: Mapped[int | None]
    observed_at: Mapped[datetime]


class SlotSnapshot(Base):
    __tablename__ = "slot_snapshots"
    __table_args__ = (
        Index("ix_slot_snapshots_lookup", "session_slug", "puln_slot_id", "observed_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_slug: Mapped[str] = mapped_column(String(80))
    puln_slot_id: Mapped[int]
    remaining: Mapped[int]
    observed_at: Mapped[datetime]


class Booking(Base):
    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    session_slug: Mapped[str] = mapped_column(String(80))
    puln_product_id: Mapped[int]
    quantity: Mapped[int]
    puln_slot_id: Mapped[int | None]
    status: Mapped[BookingStatus] = mapped_column(
        Enum(BookingStatus, native_enum=False, length=40, create_constraint=True)
    )
    operator_notes: Mapped[str | None] = mapped_column(Text)
    confirmation_ref: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, onupdate=utc_now)
    confirmed_at: Mapped[datetime | None]


class MonitorCheck(Base):
    """One row per request to the live endpoint: outcome and timing, never response secrets."""

    __tablename__ = "monitor_checks"
    __table_args__ = (Index("ix_monitor_checks_lookup", "session_slug", "started_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_slug: Mapped[str] = mapped_column(String(80))
    started_at: Mapped[datetime]
    duration_ms: Mapped[int | None]
    http_status: Mapped[int | None]
    outcome: Mapped[str] = mapped_column(String(40))
    detail: Mapped[str | None] = mapped_column(String(255))
