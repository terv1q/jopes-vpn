from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Mapping

from jopesvpn.i18n import ru


DEFAULT_LANGUAGE = "ru"
SUPPORTED_LANGUAGES: dict[str, str] = {
    "ru": "Русский",
}
LANGUAGE_FLAGS: dict[str, str] = {
    "ru": "🇷🇺",
}
_CATALOGS: dict[str, Mapping[str, Any]] = {
    "ru": ru.MESSAGES,
}
_current_language: ContextVar[str] = ContextVar("jopesvpn_language", default=DEFAULT_LANGUAGE)


def normalize_language(language: str | None) -> str:
    if language in _CATALOGS:
        return str(language)
    return DEFAULT_LANGUAGE


def current_language() -> str:
    return normalize_language(_current_language.get())


def set_language(language: str | None):
    return _current_language.set(normalize_language(language))


def reset_language(token) -> None:
    _current_language.reset(token)


def _template(key: str, language: str | None) -> Any:
    lang = normalize_language(language)
    value = _CATALOGS.get(lang, {}).get(key)
    if value is None and lang != DEFAULT_LANGUAGE:
        value = _CATALOGS[DEFAULT_LANGUAGE].get(key)
    if value is None:
        return key
    return value


def t(key: str, *, language: str | None = None, **values: Any) -> str:
    value = _template(key, language)
    if isinstance(value, Mapping):
        raise KeyError(f"Translation key {key!r} requires plural selection")
    return str(value).format(**values)


def tn(key: str, count: int, *, language: str | None = None, **values: Any) -> str:
    value = _template(key, language)
    if not isinstance(value, Mapping):
        return str(value).format(count=count, **values)
    if count % 10 == 1 and count % 100 != 11:
        form = "one"
    elif count % 10 in {2, 3, 4} and count % 100 not in {12, 13, 14}:
        form = "few"
    else:
        form = "many"
    template = value.get(form) or value.get("many") or value.get("one") or key
    return str(template).format(count=count, n=count, **values)


__all__ = [
    "DEFAULT_LANGUAGE",
    "LANGUAGE_FLAGS",
    "SUPPORTED_LANGUAGES",
    "current_language",
    "normalize_language",
    "reset_language",
    "set_language",
    "t",
    "tn",
]
