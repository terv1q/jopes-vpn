from __future__ import annotations

from contextvars import ContextVar, Token
from datetime import UTC, datetime, timedelta


DEFAULT_UTC_OFFSET = 3
ADMIN_UTC_OFFSET = 5
SUPPORTED_UTC_OFFSETS = tuple(range(-12, 15))
_current_offset: ContextVar[int] = ContextVar(
    "jopesvpn_utc_offset", default=DEFAULT_UTC_OFFSET
)


def current_utc_offset() -> int:
    return _current_offset.get()


def set_utc_offset(offset: int) -> Token[int]:
    if offset not in SUPPORTED_UTC_OFFSETS:
        raise ValueError("Unsupported UTC offset")
    return _current_offset.set(offset)


def reset_utc_offset(token: Token[int]) -> None:
    _current_offset.reset(token)


def utc_label(offset: int) -> str:
    sign = "+" if offset >= 0 else "-"
    return f"UTC{sign}{abs(offset)}"


def format_datetime(value: str | datetime | None, offset: int) -> str:
    if value is None:
        return "—"
    try:
        parsed = (
            value
            if isinstance(value, datetime)
            else datetime.fromisoformat(value.replace("Z", "+00:00"))
        )
    except ValueError:
        return "—"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    local = parsed.astimezone(UTC) + timedelta(hours=offset)
    return f"{local:%d.%m.%Y %H:%M} [{utc_label(offset)}]"
