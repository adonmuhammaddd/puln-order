"""``customers`` commands. Contact details are masked unless the operator asks to reveal them."""

from collections.abc import Callable
from typing import Any

from rich.console import Console
from rich.table import Table

from app.database.tables import Customer
from app.models.customer import (
    CustomerInput,
    CustomerUpdate,
    mask_email,
    mask_phone,
    normalize_email,
    normalize_name,
    normalize_notes,
    normalize_whatsapp,
)
from app.services.customer_service import CustomerError, CustomerService
from app.timefmt import format_local
from app.ui.commands import Runtime
from app.ui.prompts import PromptCancelled, TooManyAttempts, ask, ask_valid, confirm_word

CONSENT_QUESTION = (
    "Has this customer agreed that their name, WhatsApp number and email are stored on this "
    "computer only to coordinate PULN reservations? Type yes to confirm"
)


def _service(runtime: Runtime) -> CustomerService:
    return CustomerService(runtime.session_factory, max_active=runtime.settings.max_customers)


def _guarded(runtime: Runtime, action: Callable[[], int]) -> int:
    """Map refusals and cancelled prompts to exit codes with operator-safe messages."""
    try:
        return action()
    except CustomerError as error:
        runtime.error_console.print(str(error), markup=False, soft_wrap=True)
        return 1
    except PromptCancelled:
        runtime.console.print("Cancelled; nothing was changed.")
        return 1
    except TooManyAttempts as error:
        runtime.error_console.print(f"Too many invalid values for {error}; nothing was changed.")
        return 2


def _contact(customer: Customer, reveal: bool) -> tuple[str, str]:
    if reveal:
        return customer.whatsapp or "-", customer.email or "-"
    return mask_phone(customer.whatsapp), mask_email(customer.email)


def _status(customer: Customer) -> str:
    if not customer.consent_confirmed:
        return "no consent"
    return "active" if customer.is_active else "inactive"


def run_list(runtime: Runtime, *, active_only: bool) -> int:
    service = _service(runtime)
    table = Table(title="CUSTOMERS", title_justify="left")
    table.add_column("ID", justify="right")
    table.add_column("NAME", no_wrap=True)
    table.add_column("WHATSAPP", no_wrap=True)
    table.add_column("EMAIL", no_wrap=True)
    table.add_column("STATUS", no_wrap=True)
    for customer in service.list_all(active_only=active_only):
        whatsapp, email = _contact(customer, reveal=False)
        table.add_row(
            str(customer.id),
            customer.display_name,
            whatsapp,
            email,
            _status(customer),
            style="" if customer.is_active else "dim",
        )
    runtime.console.print(table)
    runtime.console.print(
        f"Active profiles: {service.count_active()} / {runtime.settings.max_customers}"
    )
    return 0


def _prompt_new(console: Console, service: CustomerService) -> CustomerInput:
    name = ask_valid(console, "Display name", normalize_name)
    whatsapp = ask_valid(console, "WhatsApp number", normalize_whatsapp)
    service.ensure_unique(whatsapp=whatsapp, email=None)
    email = ask_valid(console, "Email", normalize_email)
    service.ensure_unique(whatsapp=None, email=email)
    notes = ask_valid(console, "Notes (optional)", normalize_notes)
    return CustomerInput.model_validate(
        {"display_name": name, "whatsapp": whatsapp, "email": email, "notes": notes}
    )


def run_add(runtime: Runtime) -> int:
    def action() -> int:
        console = runtime.console
        service = _service(runtime)
        service.ensure_capacity()
        console.print("New customer. Use only details the customer agreed to share.")
        data = _prompt_new(console, service)
        if ask(console, CONSENT_QUESTION).strip().lower() != "yes":
            console.print("Consent not confirmed; nothing was saved.")
            return 1
        customer = service.create(data, consent_confirmed=True)
        console.print(f"Added customer {customer.id}.")
        return 0

    return _guarded(runtime, action)


def run_show(runtime: Runtime, customer_id: int, *, reveal: bool) -> int:
    def action() -> int:
        customer = _service(runtime).get(customer_id)
        tz = runtime.settings.app_timezone
        whatsapp, email = _contact(customer, reveal)
        console = runtime.console
        if reveal:
            console.print(
                "Contact details shown in full. Do not screenshot or share.", style="yellow"
            )
        for label, value in (
            ("ID", str(customer.id)),
            ("Name", customer.display_name),
            ("WhatsApp", whatsapp),
            ("Email", email),
            ("Active", "yes" if customer.is_active else "no"),
            ("Consent confirmed", "yes" if customer.consent_confirmed else "no"),
            ("Notes", customer.notes or "-"),
            ("Created", format_local(customer.created_at, tz)),
            ("Updated", format_local(customer.updated_at, tz)),
        ):
            console.print(f"{label}: {value}", markup=False, soft_wrap=True)
        return 0

    return _guarded(runtime, action)


def _clear_or_notes(value: str) -> str | None:
    return "" if value.strip() == "-" else normalize_notes(value)


def run_edit(runtime: Runtime, customer_id: int) -> int:
    def action() -> int:
        service = _service(runtime)
        current = service.get(customer_id)
        console = runtime.console
        console.print(f"Editing customer {customer_id}. Press Enter to keep a value.")
        whatsapp, email = _contact(current, reveal=False)
        changes: dict[str, Any] = {
            "display_name": ask_valid(
                console, f"Display name [{current.display_name}]", normalize_name, keep_blank=True
            ),
            "whatsapp": ask_valid(
                console, f"WhatsApp number [{whatsapp}]", normalize_whatsapp, keep_blank=True
            ),
            "email": ask_valid(console, f"Email [{email}]", normalize_email, keep_blank=True),
        }
        notes = ask_valid(
            console, "Notes (Enter to keep, - to clear)", _clear_or_notes, keep_blank=True
        )
        provided = {name: value for name, value in changes.items() if value is not None}
        if notes is not None:
            provided["notes"] = notes or None
        service.update(customer_id, CustomerUpdate.model_validate(provided))
        console.print(f"Updated customer {customer_id}.")
        return 0

    return _guarded(runtime, action)


def run_set_active(runtime: Runtime, customer_id: int, *, active: bool) -> int:
    def action() -> int:
        service = _service(runtime)
        if active:
            service.activate(customer_id)
            runtime.console.print(f"Customer {customer_id} is active.")
        else:
            service.deactivate(customer_id)
            runtime.console.print(f"Customer {customer_id} is inactive.")
        return 0

    return _guarded(runtime, action)


def run_delete(runtime: Runtime, customer_id: int, *, anonymize: bool, assume_yes: bool) -> int:
    def action() -> int:
        service = _service(runtime)
        service.get(customer_id)
        console = runtime.console
        if anonymize:
            message = f"Erase the personal data of customer {customer_id} and keep bookings?"
            word = "ANONYMIZE"
        else:
            message = f"Permanently delete customer {customer_id}?"
            word = "DELETE"
        if not assume_yes and not confirm_word(console, message, word):
            console.print("Not confirmed; nothing was changed.")
            return 1
        if anonymize:
            service.anonymize(customer_id)
            console.print(f"Customer {customer_id} anonymized.")
        else:
            service.delete(customer_id)
            console.print(f"Customer {customer_id} deleted.")
        return 0

    return _guarded(runtime, action)
