from __future__ import annotations

import hashlib
import hmac
import html
import ipaddress
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from typing import Any
from urllib.parse import parse_qs, urlsplit
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from jopesvpn.tickets import TicketStore, TicketStoreError

logger = logging.getLogger(__name__)
BASE_PATH = "/support"
MAX_REQUEST_BYTES = 12_000_000
MAX_FORM_BYTES = 12_000
MESSAGE_PAGE_SIZE = 50
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{20,128}$")
ADMIN_TICKET_PATTERN = re.compile(r"^/support/admin/ticket/([1-9][0-9]*)$")
ADMIN_POSTS_PATH = BASE_PATH + "/admin/posts"
UTC_PLUS_FIVE = timezone(timedelta(hours=5))
TELEGRAM_TAGS = {
    "b", "strong", "i", "em", "u", "ins", "s", "strike", "del",
    "code", "pre", "a", "blockquote",
}


class _TelegramHtmlSanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.stack: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "br":
            self.parts.append("<br>")
            return
        if tag in {"div", "p"}:
            if self.parts and not self.parts[-1].endswith("<br>"):
                self.parts.append("<br>")
            return
        if tag not in TELEGRAM_TAGS:
            return
        if tag == "a":
            href = dict(attrs).get("href", "")
            parsed = urlsplit(href)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or any(character.isspace() for character in href)
            ):
                return
            try:
                port = parsed.port
            except ValueError:
                return
            if port == 0:
                return
            self.parts.append(f'<a href="{html.escape(href, quote=True)}">')
        else:
            self.parts.append(f"<{tag}>")
        self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs) -> None:
        if tag == "br":
            self.parts.append("<br>")
        else:
            self.handle_starttag(tag, attrs)
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag not in self.stack:
            return
        while self.stack:
            opened = self.stack.pop()
            self.parts.append(f"</{opened}>")
            if opened == tag:
                break

    def handle_data(self, data: str) -> None:
        self.parts.append(html.escape(data, quote=False))

    def finish(self) -> str:
        while self.stack:
            self.parts.append(f"</{self.stack.pop()}>")
        return "".join(self.parts).strip()


def _sanitize_post_html(value: str) -> str:
    sanitizer = _TelegramHtmlSanitizer()
    sanitizer.feed(value)
    sanitizer.close()
    return sanitizer.finish()


class RedactingRequestHandler(WSGIRequestHandler):
    def log_message(self, format_string: str, *args: Any) -> None:
        getattr(self.server, "logger", logger).info(
            "Ticket portal request completed"
        )


def _escape(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _format_time(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(UTC_PLUS_FIVE).strftime("%d.%m.%Y %H:%M [UTC+5]")
    except (TypeError, ValueError):
        return "Время недоступно"


def _page(title: str, content: str, *, status: int = 200, headers=()):
    document = f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>{_escape(title)} — Jopes VPN</title>
<style>
:root{{color-scheme:light dark;font-family:system-ui,sans-serif}}
body{{margin:0;background:#10151d;color:#edf2f7}}
main{{max-width:880px;margin:32px auto;padding:24px}}
.card{{background:#1a2330;border:1px solid #344255;border-radius:16px;padding:20px;margin:14px 0}}
h1,h2{{line-height:1.25}}h1{{font-size:1.65rem}}h2{{font-size:1.15rem}}
.muted{{color:#b4c0ce;font-size:.92rem}}
.message{{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.55;margin-top:14px}}
label{{display:block;margin:12px 0 6px}}
textarea,input{{box-sizing:border-box;width:100%;border:1px solid #506176;border-radius:10px;padding:12px;background:#111923;color:inherit;font:inherit}}
input[type=radio],input[type=checkbox]{{width:auto}}
textarea{{min-height:130px;resize:vertical}}
button,.button{{display:inline-block;border:0;border-radius:10px;padding:11px 16px;background:#df3b49;color:#fff;font:inherit;font-weight:650;text-decoration:none;cursor:pointer;margin:8px 8px 0 0}}
.secondary{{background:#35465a}}.closed{{color:#ffcc80}}.notice{{border-left:4px solid #df3b49;padding:10px 14px;background:#222e3c}}
a{{color:#9bc8ff}}.toolbar{{display:flex;gap:10px;align-items:center;flex-wrap:wrap}}
@media(max-width:600px){{main{{margin:0 auto;padding:14px}}.card{{padding:16px}}}}
</style>
</head><body><main><h1>{_escape(title)}</h1>{content}</main></body></html>"""
    response_headers = [
        ("Content-Type", "text/html; charset=utf-8"),
        ("Cache-Control", "no-store"),
        ("X-Content-Type-Options", "nosniff"),
        ("Referrer-Policy", "no-referrer"),
        (
            "Content-Security-Policy",
            (
                "default-src 'none'; style-src 'unsafe-inline'; "
                "script-src 'unsafe-inline'; form-action 'self'; "
                "base-uri 'none'; frame-ancestors 'none'"
            ),
        ),
        *headers,
    ]
    return status, response_headers, document.encode("utf-8")


class TicketPortalApp:
    def __init__(
        self,
        tickets: TicketStore,
        *,
        admin_password: str,
        session_secret: str,
        admin_ids: tuple[int, ...],
        session_cookie_name: str = "jopes_ticket_admin",
    ) -> None:
        if (
            len(admin_password) < 20
            or len(session_secret) < 32
            or admin_password.startswith("REPLACE_WITH_")
            or session_secret.startswith("REPLACE_WITH_")
            or hmac.compare_digest(
                admin_password.encode("utf-8"), session_secret.encode("utf-8")
            )
        ):
            raise ValueError("Ticket portal credentials must be configured securely")
        if not admin_ids or any(admin_id <= 0 for admin_id in admin_ids):
            raise ValueError("At least one valid Telegram administrator is required")
        self.tickets = tickets
        self.admin_password = admin_password
        self.session_secret = session_secret.encode("utf-8")
        self.admin_ids = admin_ids
        self.session_cookie_name = session_cookie_name
        self._failed_logins: dict[str, tuple[int, float]] = {}
        self._login_lock = threading.Lock()

    def __call__(self, environ, start_response):
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = environ.get("PATH_INFO", "/")
        query = parse_qs(environ.get("QUERY_STRING", ""))
        try:
            if path == BASE_PATH:
                return self._redirect(start_response, BASE_PATH + "/")
            if path == BASE_PATH + "/":
                return self._redirect(start_response, BASE_PATH + "/admin")
            if path == BASE_PATH + "/admin" or path == BASE_PATH + "/admin/":
                if method == "POST":
                    return self._admin_login(environ, start_response)
                if not self._is_admin(environ):
                    return self._response(
                        start_response,
                        *_page("Вход для администратора", self._login_form()),
                    )
                page = self._parse_page(query)
                return self._admin_list(start_response, page)
            if path == ADMIN_POSTS_PATH:
                if not self._is_admin(environ):
                    return self._response(
                        start_response,
                        *_page("Требуется вход", self._login_form(), status=401),
                    )
                return self._admin_posts(environ, start_response, method)
            if path == BASE_PATH + "/admin/logout" and method == "POST":
                return self._redirect(
                    start_response,
                    BASE_PATH + "/admin",
                    headers=(("Set-Cookie", self._expired_cookie()),),
                )
            admin_ticket = ADMIN_TICKET_PATTERN.fullmatch(path)
            if admin_ticket:
                if not self._is_admin(environ):
                    return self._response(
                        start_response,
                        *_page("Требуется вход", self._login_form(), status=401),
                    )
                return self._admin_ticket(
                    environ, start_response, int(admin_ticket.group(1)), method
                )
            if path.startswith(BASE_PATH + "/t/"):
                return self._response(
                    start_response,
                    *_page("Не найдено", "<p>Страница не найдена.</p>", status=404),
                )
            return self._response(
                start_response, *_page("Не найдено", "<p>Страница не найдена.</p>", status=404)
            )
        except (TicketStoreError, OSError):
            return self._response(
                start_response,
                *_page(
                    "Временная ошибка",
                    '<section class="card"><p>Не удалось загрузить обращение. '
                    "Попробуйте ещё раз позже.</p></section>",
                    status=503,
                ),
            )
        except ValueError:
            return self._response(
                start_response,
                *_page(
                    "Некорректный запрос",
                    "<p>Проверьте данные и попробуйте ещё раз.</p>",
                    status=400,
                ),
            )

    @staticmethod
    def _parse_page(query: dict[str, list[str]]) -> int:
        try:
            return min(max(int(query.get("page", ["0"])[0]), 0), 10_000)
        except ValueError:
            return 0

    @staticmethod
    def _response(start_response, status: int, headers, body: bytes):
        phrase = HTTPStatus(status).phrase
        start_response(f"{status} {phrase}", headers)
        return [body]

    def _redirect(self, start_response, location: str, headers=()):
        return self._response(
            start_response,
            303,
            [("Location", location), ("Cache-Control", "no-store"), *headers],
            b"",
        )

    def _read_form(self, environ) -> dict[str, list[str]]:
        try:
            length = int(environ.get("CONTENT_LENGTH") or "0")
        except ValueError:
            raise ValueError("Invalid request length") from None
        if length < 0 or length > MAX_FORM_BYTES:
            raise ValueError("Request is too large")
        content_type = environ.get("CONTENT_TYPE", "")
        if content_type.split(";", 1)[0].strip().lower() != (
            "application/x-www-form-urlencoded"
        ):
            raise ValueError("Unsupported form encoding")
        data = environ["wsgi.input"].read(length).decode("utf-8")
        return parse_qs(data, keep_blank_values=True, max_num_fields=8)

    def _read_post_fields(self, environ):
        try:
            length = int(environ.get("CONTENT_LENGTH") or "0")
        except ValueError:
            raise ValueError("Invalid request length") from None
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise ValueError("Request is too large")
        content_type = environ.get("CONTENT_TYPE", "")
        if content_type.split(";", 1)[0].strip().lower() == (
            "application/x-www-form-urlencoded"
        ):
            data = environ["wsgi.input"].read(length).decode("utf-8")
            return parse_qs(data, keep_blank_values=True, max_num_fields=32), None
        if content_type.split(";", 1)[0].strip().lower() != "multipart/form-data":
            raise ValueError("Unsupported form encoding")
        headers = (
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n"
        ).encode("ascii", errors="strict")
        message = BytesParser(policy=policy.default).parsebytes(
            headers + environ["wsgi.input"].read(length)
        )
        fields: dict[str, list[str]] = {}
        photo: tuple[bytes, str] | None = None
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if not name or name not in {"action", "body_html", "photo"} and not re.fullmatch(
                r"button_(label|url|row)_[0-7]", name
            ):
                continue
            payload = part.get_payload(decode=True) or b""
            filename = part.get_filename()
            if filename:
                if name != "photo" or photo is not None:
                    raise ValueError("Unexpected upload")
                photo = (payload, part.get_content_type().lower())
            else:
                fields.setdefault(name, []).append(payload.decode("utf-8"))
        return fields, photo

    def _login_form(self, error: str = "") -> str:
        error_html = f'<p class="closed">{_escape(error)}</p>' if error else ""
        return (
            '<section class="card"><p class="muted">Доступ только для '
            "администратора поддержки.</p>"
            f"{error_html}<form method=\"post\" action=\"{BASE_PATH}/admin\">"
            '<label for="password">Пароль</label>'
            '<input id="password" name="password" type="password" '
            'autocomplete="current-password" required>'
            '<button type="submit">Войти</button></form></section>'
        )

    def _login_limited(self, remote_ip: str) -> bool:
        now = time.monotonic()
        with self._login_lock:
            self._failed_logins = {
                ip: entry for ip, entry in self._failed_logins.items()
                if now - entry[1] < 900
            }
            if len(self._failed_logins) >= 1024:
                oldest_ip = min(
                    self._failed_logins,
                    key=lambda ip: self._failed_logins[ip][1],
                )
                del self._failed_logins[oldest_ip]
            count, _ = self._failed_logins.get(remote_ip, (0, now))
            return count >= 10

    def _record_failed_login(self, remote_ip: str) -> None:
        now = time.monotonic()
        with self._login_lock:
            count, started = self._failed_logins.get(remote_ip, (0, now))
            self._failed_logins[remote_ip] = (count + 1, started)

    def _admin_login(self, environ, start_response):
        remote_ip = self._remote_ip(environ) or environ.get("REMOTE_ADDR", "unknown")
        if self._login_limited(remote_ip):
            return self._response(
                start_response,
                *_page("Вход ограничен", "<p>Попробуйте через 15 минут.</p>", status=429),
            )
        try:
            password = self._read_form(environ).get("password", [""])[0]
        except (UnicodeDecodeError, ValueError):
            password = ""
        if not hmac.compare_digest(
            password.encode("utf-8"), self.admin_password.encode("utf-8")
        ):
            self._record_failed_login(remote_ip)
            logger.warning("Ticket portal administrator login rejected")
            return self._response(
                start_response,
                *_page(
                    "Вход для администратора",
                    self._login_form("Неверный пароль."),
                    status=401,
                ),
            )
        with self._login_lock:
            self._failed_logins.pop(remote_ip, None)
        expiry = int(time.time()) + 12 * 60 * 60
        payload = str(expiry)
        signature = hmac.new(
            self.session_secret, payload.encode("ascii"), hashlib.sha256
        ).hexdigest()
        cookie = (
            f"{self.session_cookie_name}={payload}.{signature}; Path={BASE_PATH}; "
            "Max-Age=43200; Secure; HttpOnly; SameSite=Strict"
        )
        return self._redirect(
            start_response,
            BASE_PATH + "/admin",
            headers=(("Set-Cookie", cookie),),
        )

    def _is_admin(self, environ) -> bool:
        cookie = SimpleCookie()
        try:
            cookie.load(environ.get("HTTP_COOKIE", ""))
        except CookieError:
            return False
        morsel = cookie.get(self.session_cookie_name)
        if morsel is None:
            return False
        try:
            expiry_text, signature = morsel.value.split(".", 1)
            expiry = int(expiry_text)
        except (ValueError, TypeError):
            return False
        expected = hmac.new(
            self.session_secret, expiry_text.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return expiry > int(time.time()) and hmac.compare_digest(signature, expected)

    @staticmethod
    def _remote_ip(environ) -> str | None:
        value = environ.get("HTTP_X_REAL_IP") or environ.get("REMOTE_ADDR", "")
        try:
            return str(ipaddress.ip_address(value))
        except ValueError:
            return None

    def _admin_list(self, start_response, page: int):
        tickets = self.tickets.admin_tickets(limit=51, offset=page * 50)
        has_next_page = len(tickets) > 50
        tickets = tickets[:50]
        cards = []
        for ticket in tickets:
            last_message = _escape(ticket.get("last_message"))[:400]
            cards.append(
                '<section class="card"><div class="toolbar"><b>'
                f'#{ticket["ticket_id"]}</b><span class="muted">'
                f'{_escape(ticket["user_name"])} · Telegram ID '
                f'{int(ticket["user_id"])} · {_escape(ticket["category"])} · '
                f'{_escape(ticket["status"])} · '
                f'{_format_time(ticket["updated_at"])}</span></div>'
                f'<p class="message">{last_message}</p>'
                f'<a class="button" href="{BASE_PATH}/admin/ticket/'
                f'{int(ticket["ticket_id"])}">Открыть обращение</a></section>'
            )
        if not tickets:
            cards.append('<section class="card"><p>Обращений нет.</p></section>')
        paging = '<div class="toolbar">'
        if page:
            paging += (
                f'<a class="button secondary" href="{BASE_PATH}/admin?page={page - 1}">'
                "Предыдущие</a>"
            )
        if has_next_page:
            paging += (
                f'<a class="button secondary" href="{BASE_PATH}/admin?page={page + 1}">'
                "Следующие</a>"
            )
        paging += (
            f'<a class="button secondary" href="{ADMIN_POSTS_PATH}">Создать пост</a>'
            f'<form method="post" action="{BASE_PATH}/admin/logout">'
            '<button class="secondary" type="submit">Выйти</button></form></div>'
        )
        return self._response(
            start_response,
            *_page(
                "Обращения поддержки",
                "<p class=\"muted\">Время указано по UTC+5.</p>"
                + "".join(cards)
                + paging,
            ),
        )

    @staticmethod
    def _same_origin(environ) -> bool:
        origin = urlsplit(environ.get("HTTP_ORIGIN", ""))
        host = environ.get("HTTP_HOST", "").lower()
        return bool(host and origin.netloc.lower() == host and origin.scheme in {"http", "https"})

    def _admin_posts(self, environ, start_response, method: str):
        if method == "POST":
            if not self._same_origin(environ):
                return self._response(
                    start_response,
                    *_page("Запрос отклонён", "<p>Обновите страницу и попробуйте ещё раз.</p>", status=403),
                )
            fields, photo = self._read_post_fields(environ)
            if fields.get("action", [""])[0] != "create_post":
                return self._response(
                    start_response,
                    *_page("Некорректный запрос", "<p>Неизвестное действие.</p>", status=400),
                )
            body_html = _sanitize_post_html(fields.get("body_html", [""])[0])
            if len(body_html) > 1024:
                return self._response(
                    start_response,
                    *_page("Текст слишком длинный", "<p>Для поста Telegram доступно не более 1024 символов.</p>", status=400),
                )
            buttons: dict[int, list[dict[str, str]]] = {}
            for index in range(8):
                label = fields.get(f"button_label_{index}", [""])[0].strip()
                target = fields.get(f"button_url_{index}", [""])[0].strip()
                row_text = fields.get(f"button_row_{index}", ["1"])[0].strip()
                if not label and not target:
                    continue
                if not label or not target or len(label) > 64:
                    raise ValueError("Button label and URL are required")
                parsed = urlsplit(target)
                if (
                    parsed.scheme != "https"
                    or not parsed.hostname
                    or parsed.username
                    or any(character.isspace() for character in target)
                    or len(target) > 2048
                ):
                    raise ValueError("Button URL must use HTTPS")
                try:
                    port = parsed.port
                except ValueError:
                    raise ValueError("Button URL has an invalid port") from None
                if port == 0:
                    raise ValueError("Button URL has an invalid port")
                try:
                    row_index = int(row_text)
                except ValueError:
                    raise ValueError("Invalid button row") from None
                if not 1 <= row_index <= 4:
                    raise ValueError("Invalid button row")
                buttons.setdefault(row_index, []).append({"text": label, "url": target})
            if any(len(row) > 8 for row in buttons.values()):
                raise ValueError("Too many buttons in a row")
            normalized_buttons = [
                buttons[row] for row in sorted(buttons)
            ]
            photo_data = None
            photo_mime = None
            if photo is not None:
                photo_data, photo_mime = photo
                valid_signature = (
                    photo_mime == "image/png" and photo_data.startswith(b"\x89PNG\r\n\x1a\n")
                ) or (
                    photo_mime == "image/jpeg" and photo_data.startswith(b"\xff\xd8\xff")
                ) or (
                    photo_mime == "image/webp"
                    and photo_data[:4] == b"RIFF"
                    and photo_data[8:12] == b"WEBP"
                )
                if not valid_signature:
                    raise ValueError("Upload a PNG, JPEG, or WebP image")
                if len(photo_data) > 10_000_000:
                    raise ValueError("Image must be smaller than 10 MB")
            if not body_html and photo_data is None:
                raise ValueError("Add text or an image")
            post_id = self.tickets.create_channel_post(
                self.admin_ids[0],
                body_html,
                normalized_buttons,
                photo_data,
                photo_mime,
            )
            return self._redirect(
                start_response, f"{ADMIN_POSTS_PATH}?created={post_id}"
            )
        if method != "GET":
            return self._response(
                start_response,
                *_page("Метод не поддерживается", "<p>Используйте GET или POST.</p>", status=405),
            )
        query = parse_qs(environ.get("QUERY_STRING", ""))
        created = query.get("created", [""])[0]
        notice = (
            f'<p class="notice">Черновик #{_escape(created)} сохранён. '
            "Бот отправит владельцу предпросмотр для одобрения.</p>"
            if re.fullmatch(r"[1-9][0-9]*", created)
            else ""
        )
        button_fields = []
        for index in range(8):
            button_fields.append(
                '<section class="card"><b>Кнопка '
                f'{index + 1}</b><label for="label-{index}">Текст кнопки</label>'
                f'<input id="label-{index}" name="button_label_{index}" maxlength="64">'
                f'<label for="url-{index}">Ссылка (HTTPS)</label>'
                f'<input id="url-{index}" name="button_url_{index}" type="url" '
                'placeholder="https://example.com">'
                f'<label for="row-{index}">Ряд (1–4)</label>'
                f'<input id="row-{index}" name="button_row_{index}" type="number" '
                'min="1" max="4" value="1"></section>'
            )
        cards = []
        for post in self.tickets.channel_posts():
            cards.append(
                '<section class="card"><div class="toolbar"><b>'
                f'#{int(post["post_id"])}</b><span class="muted">'
                f'{_escape(post["status"])} · {_format_time(post["created_at"])}'
                f'{" · с изображением" if post["has_photo"] else ""}</span></div>'
                f'<p class="message">{_escape(post["body_html"])[:300]}</p></section>'
            )
        content = (
            f'<a class="button secondary" href="{BASE_PATH}/admin">К обращениям</a>'
            + notice
            + '<section class="card"><p class="muted">Пост появится в канале только '
            "после одобрения владельцем в Telegram. Для форматирования выделите "
            "текст и используйте кнопки.</p>"
            f'<form method="post" action="{ADMIN_POSTS_PATH}" enctype="multipart/form-data">'
            '<input type="hidden" name="action" value="create_post">'
            '<div class="toolbar">'
            '<button class="secondary" type="button" data-command="bold"><b>Жирный</b></button>'
            '<button class="secondary" type="button" data-command="italic"><i>Курсив</i></button>'
            '<button class="secondary" type="button" data-command="underline"><u>Подчёркнутый</u></button>'
            '<button class="secondary" type="button" data-command="strikeThrough">Зачёркнутый</button>'
            '<button class="secondary" type="button" data-command="createLink">Ссылка</button>'
            '</div><label for="post-editor">Текст поста</label>'
            '<div id="post-editor" contenteditable="true" role="textbox" '
            'aria-multiline="true" class="card" style="min-height:160px"></div>'
            '<textarea id="body-html" name="body_html" hidden></textarea>'
            '<label for="post-photo">Изображение (PNG, JPEG или WebP, до 10 МБ)</label>'
            '<input id="post-photo" name="photo" type="file" accept="image/png,image/jpeg,image/webp">'
            '<h2>Кнопки</h2>'
            + "".join(button_fields)
            + '<button type="submit">Отправить владельцу на одобрение</button></form></section>'
            + "<h2>Последние посты</h2>"
            + ("".join(cards) if cards else '<section class="card"><p>Постов пока нет.</p></section>')
            + '<script>'
            'const editor=document.getElementById("post-editor");'
            'document.querySelectorAll("[data-command]").forEach(button=>button.addEventListener("click",()=>{'
            'const command=button.dataset.command;'
            'if(command==="createLink"){const url=prompt("HTTPS-ссылка");'
            'if(url)document.execCommand(command,false,url)}else document.execCommand(command,false,null);'
            'editor.focus()}));'
            'document.querySelector("form").addEventListener("submit",()=>{'
            'document.getElementById("body-html").value=editor.innerHTML});'
            '</script>'
        )
        return self._response(start_response, *_page("Публикации канала", content))

    def _messages_html(
        self, ticket_id: int, *, admin: bool, page: int, page_url: str
    ) -> str:
        chunks = []
        messages = self.tickets.messages(
            ticket_id, limit=MESSAGE_PAGE_SIZE + 1, offset=page * MESSAGE_PAGE_SIZE
        )
        has_older = len(messages) > MESSAGE_PAGE_SIZE
        messages = messages[-MESSAGE_PAGE_SIZE:]
        pager = '<div class="toolbar">'
        if page > 0:
            pager += (
                f'<a class="button secondary" href="{page_url}?messages_page={page - 1}">'
                "Новые сообщения</a>"
            )
        if has_older:
            pager += (
                f'<a class="button secondary" href="{page_url}?messages_page={page + 1}">'
                "Старые сообщения</a>"
            )
        pager += "</div>"
        for message in messages:
            role = "Пользователь" if message["sender_role"] == "user" else "Поддержка"
            source_ip = message.get("source_ip")
            origin = (
                f" · IP: {_escape(source_ip)}"
                if source_ip
                else " · Telegram, IP не передаётся"
            )
            if not admin:
                origin = ""
            attachment = (
                '<p class="muted">Прикреплено фото'
                + (
                    "; оно доступно в Telegram-уведомлении поддержки."
                    if admin
                    else "."
                )
                + "</p>"
                if message.get("photo_file_id")
                else ""
            )
            chunks.append(
                '<section class="card"><div class="muted">'
                f'{role} · {_format_time(message["created_at"])}{origin}</div>'
                f'<div class="message">{_escape(message["body"])}</div>{attachment}'
                "</section>"
            )
        return pager + "".join(chunks) + pager

    def _admin_ticket(self, environ, start_response, ticket_id: int, method: str):
        query = parse_qs(environ.get("QUERY_STRING", ""))
        message_page = self._parse_page(
            {"page": query.get("messages_page", ["0"])}
        )
        ticket = self.tickets.get(ticket_id)
        if ticket is None:
            return self._response(
                start_response, *_page("Не найдено", "<p>Обращение не найдено.</p>", status=404)
            )
        if method == "POST":
            form = self._read_form(environ)
            action = form.get("action", ["reply"])[0]
            if action == "close":
                if self.tickets.close(ticket_id, "admin"):
                    self.tickets.queue_notification(
                        ticket_id, int(ticket["user_id"])
                    )
            elif action == "reply":
                body = form.get("body", [""])[0].strip()
                if not body or len(body) > 4000:
                    return self._response(
                        start_response,
                        *_page(
                            "Некорректный ответ",
                            "<p>Сообщение должно содержать от 1 до 4000 символов.</p>",
                            status=400,
                        ),
                    )
                source_ip = self._remote_ip(environ)
                if source_ip is None:
                    return self._response(
                        start_response,
                        *_page(
                            "Не удалось определить IP",
                            "<p>Ответ не отправлен. Обновите страницу и попробуйте ещё раз.</p>",
                            status=503,
                        ),
                    )
                admin_id = self.admin_ids[0]
                saved = self.tickets.add_message(
                    ticket_id,
                    admin_id,
                    "admin",
                    body,
                    source_ip=source_ip,
                    notification_recipients=(int(ticket["user_id"]),),
                )
                if not saved:
                    return self._response(
                        start_response,
                        *_page(
                            "Обращение закрыто",
                            "<p>Ответить в закрытом обращении нельзя.</p>",
                            status=409,
                        ),
                    )
            else:
                return self._response(
                    start_response,
                    *_page(
                        "Некорректный запрос",
                        "<p>Неизвестное действие.</p>",
                        status=400,
                    ),
                )
            return self._redirect(start_response, f"{BASE_PATH}/admin/ticket/{ticket_id}")
        status = (
            '<p class="closed">Обращение закрыто. Повторно открыть его нельзя.</p>'
            if ticket["status"] != "open"
            else (
                f'<form method="post" action="{BASE_PATH}/admin/ticket/{ticket_id}">'
                '<input type="hidden" name="action" value="reply">'
                '<label for="body">Ответ</label><textarea id="body" name="body" '
                'maxlength="4000" required></textarea>'
                '<button type="submit">Отправить ответ</button></form>'
                f'<form method="post" action="{BASE_PATH}/admin/ticket/{ticket_id}">'
                '<input type="hidden" name="action" value="close">'
                '<button class="secondary" type="submit">Закрыть обращение</button></form>'
            )
        )
        content = (
            f'<p class="muted">#{ticket_id} · {_escape(ticket["user_name"])} · '
            f'Telegram ID {int(ticket["user_id"])} · '
            f'{_escape(ticket["category"])} · {_escape(ticket["status"])}</p>'
            '<p class="notice">IP-адреса и время ответов фиксируются для поддержки; '
            "IP доступны только администратору и удаляются через 90 дней.</p>"
            + self._messages_html(
                ticket_id,
                admin=True,
                page=message_page,
                page_url=f"{BASE_PATH}/admin/ticket/{ticket_id}",
            )
            + f'<section class="card">{status}</section>'
            + f'<a class="button secondary" href="{BASE_PATH}/admin/ticket/{ticket_id}">'
            "Обновить переписку</a>"
            + f'<a class="button secondary" href="{BASE_PATH}/admin">К списку</a>'
        )
        return self._response(start_response, *_page(f"Обращение #{ticket_id}", content))

    def _user_ticket(self, environ, start_response, token: str, method: str):
        query = parse_qs(environ.get("QUERY_STRING", ""))
        message_page = self._parse_page(
            {"page": query.get("messages_page", ["0"])}
        )
        if not TOKEN_PATTERN.fullmatch(token):
            return self._response(
                start_response,
                *_page(
                    "Ссылка недействительна",
                    "<p>Проверьте ссылку из бота.</p>",
                    status=404,
                ),
            )
        ticket = self.tickets.portal_ticket(token)
        if ticket is None:
            return self._response(
                start_response,
                *_page(
                    "Ссылка недействительна",
                    "<p>Проверьте ссылку из бота.</p>",
                    status=404,
                ),
            )
        ticket_id = int(ticket["ticket_id"])
        if method == "POST":
            form = self._read_form(environ)
            action = form.get("action", ["reply"])[0]
            if action == "rating":
                try:
                    rating = int(form.get("rating", ["0"])[0])
                except ValueError:
                    rating = 0
                saved = self.tickets.rate(ticket_id, int(ticket["user_id"]), rating)
                if not saved:
                    message = (
                        "Оценка уже сохранена."
                        if ticket["rating"] is not None
                        else "Оценить можно после ответа поддержки и закрытия."
                    )
                    return self._response(
                        start_response,
                        *_page("Оценка не сохранена", f"<p>{message}</p>", status=409),
                    )
            elif action == "reply":
                body = form.get("body", [""])[0].strip()
                if ticket["status"] != "open":
                    return self._response(
                        start_response,
                        *_page(
                            "Обращение закрыто",
                            "<p>Ответить в закрытом обращении нельзя.</p>",
                            status=409,
                        ),
                    )
                if form.get("consent") != ["on"]:
                    return self._response(
                        start_response,
                        *_page(
                            "Требуется согласие",
                            "<p>Для отправки ответа подтвердите уведомление о сборе IP-адреса.</p>",
                            status=400,
                        ),
                    )
                if not body or len(body) > 4000:
                    return self._response(
                        start_response,
                        *_page(
                            "Некорректное сообщение",
                            "<p>Сообщение должно содержать от 1 до 4000 символов.</p>",
                            status=400,
                        ),
                    )
                source_ip = self._remote_ip(environ)
                if source_ip is None:
                    return self._response(
                        start_response,
                        *_page(
                            "Не удалось определить IP",
                            "<p>Ответ не отправлен. Обновите страницу и попробуйте ещё раз.</p>",
                            status=503,
                        ),
                    )
                saved = self.tickets.add_message(
                    ticket_id,
                    int(ticket["user_id"]),
                    "user",
                    body,
                    source_ip=source_ip,
                    notification_recipients=self.admin_ids,
                )
                if not saved:
                    return self._response(
                        start_response,
                        *_page(
                            "Обращение закрыто",
                            "<p>Ответить в закрытом обращении нельзя.</p>",
                            status=409,
                        ),
                    )
            else:
                return self._response(
                    start_response,
                    *_page(
                        "Некорректный запрос",
                        "<p>Неизвестное действие.</p>",
                        status=400,
                    ),
                )
            return self._redirect(start_response, f"{BASE_PATH}/t/{token}")

        if ticket["status"] == "open":
            action_form = (
                f'<form method="post" action="{BASE_PATH}/t/{_escape(token)}">'
                '<input type="hidden" name="action" value="reply">'
                '<label for="body">Ваш ответ</label><textarea id="body" name="body" '
                'maxlength="4000" required></textarea>'
                '<label><input type="checkbox" name="consent" value="on" required> '
                "Я согласен(на) на сохранение IP-адреса и времени этого ответа "
                "для работы поддержки.</label>"
                '<button type="submit">Отправить</button></form>'
            )
        elif ticket["rating"] is None and self.tickets.has_admin_reply(ticket_id):
            options = "".join(
                f'<label><input type="radio" name="rating" value="{score}" required> {score}</label>'
                for score in range(1, 6)
            )
            action_form = (
                f'<form method="post" action="{BASE_PATH}/t/{_escape(token)}">'
                '<input type="hidden" name="action" value="rating">'
                "<p>Оцените помощь поддержки от 1 до 5:</p>"
                f"{options}<button type=\"submit\">Сохранить оценку</button></form>"
            )
        else:
            action_form = (
                f'<p class="closed">Обращение закрыто.'
                + (
                    f' Ваша оценка: {int(ticket["rating"])} из 5.'
                    if ticket["rating"] is not None
                    else ""
                )
                + "</p>"
            )
        content = (
            f'<p class="muted">Обращение #{ticket_id} · '
            f'{_escape(ticket["status"])}</p>'
            '<p class="notice">При отправке ответа Jopes VPN сохраняет ваш '
            "IP-адрес и время сообщения для поддержки. IP виден только "
            "администратору поддержки и удаляется через 90 дней.</p>"
            + self._messages_html(
                ticket_id,
                admin=False,
                page=message_page,
                page_url=f"{BASE_PATH}/t/{_escape(token)}",
            )
            + f'<section class="card">{action_form}</section>'
            + f'<a class="button secondary" href="{BASE_PATH}/t/{_escape(token)}">'
            "Обновить переписку</a>"
        )
        return self._response(
            start_response, *_page(f"Ваше обращение #{ticket_id}", content)
        )


def _admin_ids_from_env() -> tuple[int, ...]:
    try:
        admin_ids = list(
            dict.fromkeys(
                int(value.strip()) for value in os.environ["BOT_ADMIN_IDS"].split(",")
                if value.strip()
            )
        )
    except (KeyError, ValueError):
        raise RuntimeError("BOT_ADMIN_IDS must contain Telegram administrator IDs") from None
    if not admin_ids:
        raise RuntimeError("BOT_ADMIN_IDS must contain Telegram administrator IDs")
    owner_id = os.environ.get("BOT_OWNER_ID", "").strip()
    if not owner_id:
        owner = min(admin_ids)
    else:
        try:
            owner = int(owner_id)
        except ValueError:
            raise RuntimeError("BOT_OWNER_ID must be a Telegram administrator ID") from None
        if owner not in admin_ids:
            raise RuntimeError("BOT_OWNER_ID must be included in BOT_ADMIN_IDS")
        admin_ids.remove(owner)
        admin_ids.insert(0, owner)
    return tuple(admin_ids)


def run() -> None:
    from pathlib import Path

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    database = Path(os.environ.get("BOT_STATE_DB", "/app/data/bot-state.sqlite3"))
    tickets = TicketStore(database)
    try:
        expired = tickets.purge_old_source_ips()
        if expired:
            logging.info("Purged expired support IP records")
    except TicketStoreError:
        logging.warning("Initial support IP retention cleanup failed")

    def purge_ip_history() -> None:
        while True:
            time.sleep(24 * 60 * 60)
            try:
                removed = tickets.purge_old_source_ips()
                if removed:
                    logging.info("Purged expired support IP records")
            except TicketStoreError:
                logging.warning("Support IP retention cleanup failed")

    threading.Thread(target=purge_ip_history, daemon=True, name="ticket-ip-retention").start()
    app = TicketPortalApp(
        tickets,
        admin_password=os.environ.get("TICKET_ADMIN_PASSWORD", ""),
        session_secret=os.environ.get("TICKET_SESSION_SECRET", ""),
        admin_ids=_admin_ids_from_env(),
    )
    server = make_server(
        "0.0.0.0",
        int(os.environ.get("TICKET_PORT", "8080")),
        app,
        server_class=WSGIServer,
        handler_class=RedactingRequestHandler,
    )
    setattr(server, "logger", logging.getLogger("ticket_portal.http"))
    logging.info("Ticket portal listening on its private container network")
    server.serve_forever()


if __name__ == "__main__":
    run()
