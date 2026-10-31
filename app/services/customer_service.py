"""Customer profiles for up to the configured number of consenting customers.

Logs refer to customers by ID only; personal data never reaches the log file.
"""

import logging
from collections.abc import Sequence

from sqlalchemy.orm import Session, sessionmaker

from app.database.tables import Customer
from app.models.customer import CustomerInput, CustomerUpdate
from app.repositories import booking_repository, customer_repository

logger = logging.getLogger(__name__)


class CustomerError(ValueError):
    """A customer operation was refused; the message is safe to show the operator."""


class CustomerNotFoundError(CustomerError):
    pass


class CustomerLimitError(CustomerError):
    pass


class ConsentRequiredError(CustomerError):
    pass


class DuplicateCustomerError(CustomerError):
    pass


class CustomerHasBookingsError(CustomerError):
    pass


class CustomerService:
    def __init__(self, session_factory: sessionmaker[Session], *, max_active: int) -> None:
        self._session_factory = session_factory
        self._max_active = max_active

    @staticmethod
    def _require(session: Session, customer_id: int) -> Customer:
        customer = customer_repository.get(session, customer_id)
        if customer is None:
            raise CustomerNotFoundError(f"customer {customer_id} does not exist")
        return customer

    def _check_capacity(self, session: Session) -> None:
        if customer_repository.count_active(session) >= self._max_active:
            raise CustomerLimitError(
                f"already {self._max_active} active customers; deactivate one first"
            )

    @staticmethod
    def _check_duplicates(
        session: Session, *, whatsapp: str | None, email: str | None, exclude_id: int | None
    ) -> None:
        for other in customer_repository.find_conflicts(
            session, whatsapp=whatsapp, email=email, exclude_id=exclude_id
        ):
            field = "whatsapp" if whatsapp and other.whatsapp == whatsapp else "email"
            raise DuplicateCustomerError(f"customer {other.id} already uses this {field}")

    def ensure_capacity(self) -> None:
        """Fail early, before the operator types any personal data."""
        with self._session_factory() as session:
            self._check_capacity(session)

    def ensure_unique(
        self, *, whatsapp: str | None, email: str | None, exclude_id: int | None = None
    ) -> None:
        with self._session_factory() as session:
            self._check_duplicates(session, whatsapp=whatsapp, email=email, exclude_id=exclude_id)

    def create(self, data: CustomerInput, *, consent_confirmed: bool) -> Customer:
        if not consent_confirmed:
            raise ConsentRequiredError("the customer's consent must be confirmed before saving")
        with self._session_factory() as session:
            self._check_capacity(session)
            self._check_duplicates(
                session, whatsapp=data.whatsapp, email=data.email, exclude_id=None
            )
            customer = Customer(
                display_name=data.display_name,
                whatsapp=data.whatsapp,
                email=data.email,
                notes=data.notes,
                consent_confirmed=True,
            )
            session.add(customer)
            session.commit()
        logger.info("created customer %d", customer.id)
        return customer

    def get(self, customer_id: int) -> Customer:
        with self._session_factory() as session:
            return self._require(session, customer_id)

    def list_all(self, *, active_only: bool = False) -> Sequence[Customer]:
        with self._session_factory() as session:
            return customer_repository.list_customers(session, active_only=active_only)

    def count_active(self) -> int:
        with self._session_factory() as session:
            return customer_repository.count_active(session)

    def update(self, customer_id: int, changes: CustomerUpdate) -> Customer:
        fields = changes.model_dump(exclude_unset=True)
        with self._session_factory() as session:
            customer = self._require(session, customer_id)
            self._check_duplicates(
                session,
                whatsapp=fields.get("whatsapp"),
                email=fields.get("email"),
                exclude_id=customer_id,
            )
            for name, value in fields.items():
                if value is None and name != "notes":
                    continue
                setattr(customer, name, value)
            session.commit()
        logger.info("updated customer %d (%s)", customer_id, ", ".join(sorted(fields)) or "none")
        return customer

    def deactivate(self, customer_id: int) -> Customer:
        with self._session_factory() as session:
            customer = self._require(session, customer_id)
            customer.is_active = False
            session.commit()
        logger.info("deactivated customer %d", customer_id)
        return customer

    def activate(self, customer_id: int) -> Customer:
        with self._session_factory() as session:
            customer = self._require(session, customer_id)
            if customer.is_active:
                return customer
            if not customer.consent_confirmed:
                raise ConsentRequiredError(
                    f"customer {customer_id} has no recorded consent; add them again instead"
                )
            self._check_capacity(session)
            customer.is_active = True
            session.commit()
        logger.info("activated customer %d", customer_id)
        return customer

    def delete(self, customer_id: int) -> None:
        """Delete a customer who has no booking history."""
        with self._session_factory() as session:
            customer = self._require(session, customer_id)
            if booking_repository.customer_has_bookings(session, customer_id):
                raise CustomerHasBookingsError(
                    f"customer {customer_id} has booking history; anonymize instead to erase "
                    "their personal data and keep the bookings"
                )
            session.delete(customer)
            session.commit()
        logger.info("deleted customer %d", customer_id)

    def anonymize(self, customer_id: int) -> Customer:
        """Erase personal data but keep the record so booking history stays consistent."""
        with self._session_factory() as session:
            customer = self._require(session, customer_id)
            customer.display_name = f"Deleted customer #{customer_id}"
            customer.whatsapp = ""
            customer.email = ""
            customer.notes = None
            customer.is_active = False
            customer.consent_confirmed = False
            session.commit()
        logger.info("anonymized customer %d", customer_id)
        return customer
