"""Interactive prompts. Personal data is typed here, never passed as CLI flags, so it does not
end up in shell history."""

from collections.abc import Callable

from rich.console import Console

MAX_ATTEMPTS = 3


class PromptCancelled(Exception):
    """The operator ended input (Ctrl+D / Ctrl+C)."""


class TooManyAttempts(Exception):
    """The operator gave invalid input too many times."""


def ask(console: Console, label: str) -> str:
    try:
        return console.input(f"{label}: ")
    except (EOFError, KeyboardInterrupt):
        raise PromptCancelled from None


def ask_valid[T](
    console: Console,
    label: str,
    parse: Callable[[str], T],
    *,
    keep_blank: bool = False,
) -> T | None:
    """Ask until ``parse`` accepts the answer. With ``keep_blank``, Enter returns None."""
    for _ in range(MAX_ATTEMPTS):
        answer = ask(console, label)
        if keep_blank and not answer.strip():
            return None
        try:
            return parse(answer)
        except ValueError as error:
            console.print(f"  {error}", style="red", markup=False)
    raise TooManyAttempts(label)


def confirm_word(console: Console, message: str, word: str) -> bool:
    return ask(console, f"{message} Type {word} to confirm").strip() == word
