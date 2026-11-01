"""Read-only backend adapter for the public PULN session interface.

This adapter deliberately exposes session inspection only. A reservation-writing adapter must
not be added until PULN documents or explicitly authorises an integration contract.
"""

from dataclasses import dataclass
from typing import Protocol

from app.clients.puln_client import FetchResult, LiveOk, PageCheck, PulnClient
from app.services.session_service import SessionRef


@dataclass(frozen=True)
class BackendSessionInspection:
    """One inspection of the official page and its public live status."""

    page: PageCheck
    live: FetchResult

    @property
    def accepting(self) -> bool | None:
        """Return accepting state only when the live response was validated."""
        if isinstance(self.live, LiveOk):
            return self.live.status.accepting
        return None


class SessionBackendAdapter(Protocol):
    """Contract for read-only session inspection adapters."""

    def inspect(self, session: SessionRef) -> BackendSessionInspection: ...


class PulnReadOnlyBackendAdapter:
    """Adapt the existing conservative PulnClient to the orchestration layer."""

    def __init__(self, client: PulnClient) -> None:
        self._client = client

    def inspect(self, session: SessionRef) -> BackendSessionInspection:
        return BackendSessionInspection(
            page=self._client.check_page(session.page_url),
            live=self._client.fetch_live(session.live_url),
        )
