import json
from io import StringIO
from pathlib import Path

import httpx
from rich.console import Console

from app.config import Settings
from app.database.engine import create_session_factory, init_db
from app.models.booking import BookingStatus
from app.models.customer import CustomerInput
from app.repositories import product_repository
from app.services.booking_service import BookingService, PreparationRequest
from app.services.customer_service import CustomerService
from app.ui.commands import Runtime
from app.ui.order_commands import run_evaluate

SLUG = "online-order-20261031"


def make_runtime(tmp_path: Path, *, open_url=lambda _url: True):
    engine = init_db(f"sqlite:///{tmp_path / 'orders.db'}")
    factory = create_session_factory(engine)
    with factory() as db:
        product_repository.seed_known_products(db)
        db.commit()

    customer = CustomerService(factory, max_active=10).create(
        CustomerInput(
            display_name="Synthetic Customer",
            whatsapp="081200000001",
            email="customer@example.test",
        ),
        consent_confirmed=True,
    )
    booking_service = BookingService(factory, session_slug=SLUG)
    booking = booking_service.prepare(
        PreparationRequest(
            customer_id=customer.id,
            product_id=100,
            quantity=1,
            slot_id=None,
            notes=None,
        ),
        ready=True,
    )

    payload = json.loads(
        (Path(__file__).parent / "fixtures" / "live_sample.json").read_text()
    )
    for product in payload["products"]:
        if product["id"] == 100:
            product["stock_remaining"] = 5
    live_body = json.dumps(payload).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/live.json"):
            return httpx.Response(
                200, content=live_body, headers={"content-type": "application/json"}
            )
        return httpx.Response(200, text="<html><body>PULN session</body></html>")

    output = StringIO()
    runtime = Runtime(
        settings=Settings(
            _env_file=None,
            puln_session_slug=SLUG,
            database_url=f"sqlite:///{tmp_path / 'orders.db'}",
        ),
        engine=engine,
        session_factory=factory,
        console=Console(file=output, force_terminal=False),
        error_console=Console(file=StringIO(), force_terminal=False),
        transport=httpx.MockTransport(handler),
        open_url=open_url,
    )
    return runtime, booking_service, booking, output, engine


def test_evaluate_opens_only_first_eligible_booking(tmp_path: Path) -> None:
    opened: list[str] = []
    runtime, bookings, booking, output, engine = make_runtime(
        tmp_path, open_url=lambda url: opened.append(url) or True
    )
    try:
        assert run_evaluate(runtime, open_next=True) == 0
        assert opened == [f"https://puln.seatin.id/c/{SLUG}"]
        assert bookings.get(booking.id).status is BookingStatus.OFFICIAL_FLOW_OPENED
        assert "READY_FOR_OPERATOR" in output.getvalue()
        assert "Complete the form" in output.getvalue()
    finally:
        engine.dispose()


def test_failed_browser_open_leaves_booking_ready(tmp_path: Path) -> None:
    runtime, bookings, booking, _output, engine = make_runtime(
        tmp_path, open_url=lambda _url: False
    )
    try:
        assert run_evaluate(runtime, open_next=True) == 1
        assert bookings.get(booking.id).status is BookingStatus.READY
    finally:
        engine.dispose()
