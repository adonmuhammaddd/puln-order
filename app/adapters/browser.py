"""Playwright-based assisted browser session for the official PULN page.

The adapter opens and inspects the page only. It does not fill fields, click controls, submit
forms, solve challenges, inspect cookies, or persist browser state. The operator completes the
official flow directly in the visible browser window.

Install the optional dependency with ``uv sync --extra browser`` and then
``uv run playwright install chromium``.
"""

from dataclasses import dataclass
from enum import StrEnum
from importlib import import_module
from types import TracebackType
from typing import Any
from urllib.parse import urlsplit


class BrowserStage(StrEnum):
    CHALLENGE = "CHALLENGE"
    RESERVATION_FORM = "RESERVATION_FORM"
    SUCCESS_PAGE = "SUCCESS_PAGE"
    FAILURE_PAGE = "FAILURE_PAGE"
    UNKNOWN = "UNKNOWN"


class BrowserAdapterError(RuntimeError):
    """The official browser session could not be opened or inspected."""


@dataclass(frozen=True)
class BrowserInspection:
    url: str
    title: str
    http_status: int | None
    stage: BrowserStage


def validate_official_url(url: str) -> str:
    """Accept only HTTPS URLs on the official PULN host."""
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != "puln.seatin.id":
        raise BrowserAdapterError("Only https://puln.seatin.id URLs are allowed")
    if parts.username or parts.password:
        raise BrowserAdapterError("Credentials are not allowed in the session URL")
    return url


def classify_visible_page(text: str) -> BrowserStage:
    """Classify visible page copy without extracting form values or challenge tokens."""
    content = " ".join(text.casefold().split())
    challenge_terms = ("verify you are human", "checking your browser", "just a moment")
    if any(term in content for term in challenge_terms):
        return BrowserStage.CHALLENGE
    if "reservasi berhasil" in content:
        return BrowserStage.SUCCESS_PAGE
    if any(term in content for term in ("reservasi gagal", "reservation failed")):
        return BrowserStage.FAILURE_PAGE
    if "reservasi" in content and any(
        term in content for term in ("nama", "whatsapp", "email")
    ):
        return BrowserStage.RESERVATION_FORM
    return BrowserStage.UNKNOWN


class PlaywrightBrowserAdapter:
    """Manage one ephemeral, visible Playwright browser session for operator assistance."""

    def __init__(self) -> None:
        self._playwright: Any | None = None
        self._browser: Any | None = None
        self._context: Any | None = None
        self._page: Any | None = None

    async def __aenter__(self) -> "PlaywrightBrowserAdapter":
        try:
            async_playwright = import_module("playwright.async_api").async_playwright
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=False)
            # A fresh, non-persistent context: no saved profile, cookies, or local storage.
            self._context = await self._browser.new_context()
            self._page = await self._context.new_page()
        except ImportError as error:
            raise BrowserAdapterError(
                "Playwright is optional; install it with 'uv sync --extra browser'"
            ) from error
        except Exception as error:
            await self.close()
            raise BrowserAdapterError("Could not start the visible browser") from error
        return self

    async def open_session(self, url: str) -> BrowserInspection:
        """Navigate to the official page and report coarse visible page state."""
        validate_official_url(url)
        if self._page is None:
            raise BrowserAdapterError("Use the adapter inside 'async with'")
        try:
            response = await self._page.goto(url, wait_until="domcontentloaded")
            title = await self._page.title()
            # Read visible copy only for coarse classification; never return or persist it.
            visible_copy = await self._page.locator("body").inner_text(timeout=5_000)
            stage = classify_visible_page(visible_copy)
            return BrowserInspection(
                url=self._page.url,
                title=title,
                http_status=response.status if response is not None else None,
                stage=stage,
            )
        except Exception as error:
            raise BrowserAdapterError("Could not inspect the official PULN page") from error

    async def close(self) -> None:
        """Close the ephemeral context and browser; no browser state is persisted."""
        if self._context is not None:
            await self._context.close()
            self._context = None
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
        self._page = None

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()
