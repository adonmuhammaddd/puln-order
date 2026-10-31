import logging

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.database.engine import create_session_factory, init_db
from app.database.tables import Booking, Customer
from app.models.booking import BookingStatus
from app.models.customer import CustomerInput, CustomerUpdate
from app.services.customer_service import (
    ConsentRequiredError,
    CustomerHasBookingsError,
    CustomerLimitError,
    CustomerNotFoundError,
    CustomerService,
    DuplicateCustomerError,
)


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    return create_session_factory(init_db("sqlite://"))


@pytest.fixture
def service(session_factory: sessionmaker[Session]) -> CustomerService:
    return CustomerService(session_factory, max_active=3)


def synthetic(n: int) -> CustomerInput:
    return CustomerInput(
        display_name=f"Synthetic Customer {n}",
        whatsapp=f"08120000{n:04d}",
        email=f"customer{n}@example.test",
    )


def add_booking(session_factory: sessionmaker[Session], customer_id: int) -> None:
    with session_factory() as db:
        db.add(
            Booking(
                customer_id=customer_id,
                session_slug="online-order-20261031",
                puln_product_id=100,
                quantity=1,
                status=BookingStatus.DRAFT,
            )
        )
        db.commit()


def test_create_stores_normalised_customer_with_consent(service: CustomerService) -> None:
    customer = service.create(synthetic(1), consent_confirmed=True)

    stored = service.get(customer.id)
    assert stored.display_name == "Synthetic Customer 1"
    assert stored.whatsapp == "+6281200000001"
    assert stored.consent_confirmed is True
    assert stored.is_active is True


def test_create_without_consent_stores_nothing(service: CustomerService) -> None:
    with pytest.raises(ConsentRequiredError):
        service.create(synthetic(1), consent_confirmed=False)

    assert service.list_all() == []


def test_maximum_active_customers_is_enforced(service: CustomerService) -> None:
    for n in range(3):
        service.create(synthetic(n), consent_confirmed=True)

    with pytest.raises(CustomerLimitError, match="3"):
        service.create(synthetic(9), consent_confirmed=True)


def test_inactive_customers_do_not_count_towards_limit(service: CustomerService) -> None:
    customers = [service.create(synthetic(n), consent_confirmed=True) for n in range(3)]
    service.deactivate(customers[0].id)

    service.create(synthetic(9), consent_confirmed=True)

    assert service.count_active() == 3


def test_activate_respects_limit(service: CustomerService) -> None:
    customers = [service.create(synthetic(n), consent_confirmed=True) for n in range(3)]
    service.deactivate(customers[0].id)
    service.create(synthetic(9), consent_confirmed=True)

    with pytest.raises(CustomerLimitError):
        service.activate(customers[0].id)


def test_deactivate_then_activate(service: CustomerService) -> None:
    customer = service.create(synthetic(1), consent_confirmed=True)

    service.deactivate(customer.id)
    assert service.get(customer.id).is_active is False

    service.activate(customer.id)
    assert service.get(customer.id).is_active is True


@pytest.mark.parametrize("field", ["whatsapp", "email"])
def test_duplicate_contact_details_are_rejected(service: CustomerService, field: str) -> None:
    service.create(synthetic(1), consent_confirmed=True)
    other = synthetic(2).model_copy(update={field: getattr(synthetic(1), field)})

    with pytest.raises(DuplicateCustomerError, match=field):
        service.create(other, consent_confirmed=True)


def test_update_changes_only_given_fields(service: CustomerService) -> None:
    customer = service.create(synthetic(1), consent_confirmed=True)
    before = service.get(customer.id)

    service.update(customer.id, CustomerUpdate(email="new.address@example.test"))

    after = service.get(customer.id)
    assert after.email == "new.address@example.test"
    assert after.whatsapp == before.whatsapp
    assert after.display_name == before.display_name
    assert after.updated_at >= before.updated_at


def test_update_can_clear_notes(service: CustomerService) -> None:
    data = synthetic(1).model_copy(update={"notes": "prefers 12:00"})
    customer = service.create(data, consent_confirmed=True)

    service.update(customer.id, CustomerUpdate(notes=None))

    assert service.get(customer.id).notes is None


def test_update_rejects_duplicate_of_another_customer(service: CustomerService) -> None:
    service.create(synthetic(1), consent_confirmed=True)
    second = service.create(synthetic(2), consent_confirmed=True)

    with pytest.raises(DuplicateCustomerError):
        service.update(second.id, CustomerUpdate(email=synthetic(1).email))


def test_update_allows_keeping_own_contact_details(service: CustomerService) -> None:
    customer = service.create(synthetic(1), consent_confirmed=True)

    service.update(customer.id, CustomerUpdate(email=synthetic(1).email))


def test_unknown_customer_raises_not_found(service: CustomerService) -> None:
    with pytest.raises(CustomerNotFoundError, match="42"):
        service.get(42)
    with pytest.raises(CustomerNotFoundError):
        service.deactivate(42)


def test_list_orders_by_id_and_can_filter_active(service: CustomerService) -> None:
    first = service.create(synthetic(1), consent_confirmed=True)
    second = service.create(synthetic(2), consent_confirmed=True)
    service.deactivate(first.id)

    assert [c.id for c in service.list_all()] == [first.id, second.id]
    assert [c.id for c in service.list_all(active_only=True)] == [second.id]


def test_delete_removes_customer_without_bookings(
    service: CustomerService, session_factory: sessionmaker[Session]
) -> None:
    customer = service.create(synthetic(1), consent_confirmed=True)

    service.delete(customer.id)

    with session_factory() as db:
        assert db.scalars(select(Customer)).all() == []


def test_delete_refuses_when_booking_history_exists(
    service: CustomerService, session_factory: sessionmaker[Session]
) -> None:
    customer = service.create(synthetic(1), consent_confirmed=True)
    add_booking(session_factory, customer.id)

    with pytest.raises(CustomerHasBookingsError, match="anonymi"):
        service.delete(customer.id)

    assert service.get(customer.id).display_name == "Synthetic Customer 1"


def test_anonymize_erases_personal_data_but_keeps_bookings(
    service: CustomerService, session_factory: sessionmaker[Session]
) -> None:
    data = synthetic(1).model_copy(update={"notes": "lives near the store"})
    customer = service.create(data, consent_confirmed=True)
    add_booking(session_factory, customer.id)

    service.anonymize(customer.id)

    stored = service.get(customer.id)
    assert stored.display_name == f"Deleted customer #{customer.id}"
    assert (stored.whatsapp, stored.email, stored.notes) == ("", "", None)
    assert stored.is_active is False
    assert stored.consent_confirmed is False
    with session_factory() as db:
        assert len(db.scalars(select(Booking)).all()) == 1


def test_anonymized_customer_cannot_be_reactivated(service: CustomerService) -> None:
    customer = service.create(synthetic(1), consent_confirmed=True)
    service.anonymize(customer.id)

    with pytest.raises(ConsentRequiredError):
        service.activate(customer.id)


def test_service_never_logs_personal_data(
    service: CustomerService, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    customer = service.create(synthetic(1), consent_confirmed=True)
    service.update(customer.id, CustomerUpdate(display_name="Another Synthetic Name"))
    service.deactivate(customer.id)

    assert "Synthetic" not in caplog.text
    assert "example.test" not in caplog.text
    assert "812" not in caplog.text
    assert f"customer {customer.id}" in caplog.text
