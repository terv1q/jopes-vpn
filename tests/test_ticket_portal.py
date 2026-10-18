from __future__ import annotations

import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from jopesvpn.ticket_portal import TicketPortalApp
from jopesvpn.tickets import TicketStore


class TicketPortalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.store = TicketStore(Path(self.directory.name) / "tickets.sqlite3")
        self.ticket_id = self.store.create(
            101,
            "Tester",
            "connection",
            "<script>alert(1)</script>",
        )
        self.token = self.store.create_portal_token(self.ticket_id, 101)
        self.app = TicketPortalApp(
            self.store,
            admin_password="a-secure-test-password-long",
            session_secret="test-session-secret-that-is-long-enough",
            admin_ids=(900,),
        )

    def tearDown(self) -> None:
        self.directory.cleanup()

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        fields: dict[str, str] | None = None,
        cookie: str = "",
        remote_ip: str = "172.30.50.20",
        real_ip: str = "203.0.113.10",
    ) -> tuple[str, dict[str, str], str]:
        payload = urlencode(fields or {}).encode("utf-8")
        parsed_path = urlsplit(path)
        status: list[str] = []
        headers: dict[str, str] = {}

        def start_response(status_line, response_headers) -> None:
            status.append(status_line)
            headers.update(response_headers)

        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": parsed_path.path,
            "QUERY_STRING": parsed_path.query,
            "CONTENT_TYPE": "application/x-www-form-urlencoded",
            "CONTENT_LENGTH": str(len(payload)),
            "wsgi.input": BytesIO(payload),
            "REMOTE_ADDR": remote_ip,
            "HTTP_X_REAL_IP": real_ip,
            "HTTP_COOKIE": cookie,
        }
        body = b"".join(self.app(environ, start_response)).decode("utf-8")
        return status[0], headers, body

    def test_user_ticket_capability_urls_are_not_published(self) -> None:
        status, headers, _ = self.request("/support/")
        self.assertTrue(status.startswith("303"))
        self.assertEqual(headers["Location"], "/support/admin")

        for method in ("GET", "POST"):
            status, _, _ = self.request(
                f"/support/t/{self.token}",
                method=method,
                fields={"action": "reply", "body": "Не должно сохраниться"},
            )
            self.assertTrue(status.startswith("404"))
        self.assertEqual(len(self.store.messages(self.ticket_id)), 1)

    def test_admin_login_reply_close_and_rating_flow(self) -> None:
        status, _, body = self.request("/support/admin")
        self.assertTrue(status.startswith("200"))
        self.assertIn("Вход для администратора", body)
        self.assertNotIn("Обращение поддержки</h1>", body)

        status, headers, _ = self.request(
            "/support/admin",
            method="POST",
            fields={"password": "a-secure-test-password-long"},
        )
        self.assertTrue(status.startswith("303"))
        cookie = headers["Set-Cookie"].split(";", 1)[0]

        status, _, _ = self.request(
            f"/support/admin/ticket/{self.ticket_id}",
            method="POST",
            fields={"action": "reply", "body": "Проверим настройки."},
            cookie=cookie,
        )
        self.assertTrue(status.startswith("303"))
        admin_page_status, _, admin_page = self.request(
            f"/support/admin/ticket/{self.ticket_id}", cookie=cookie
        )
        self.assertTrue(admin_page_status.startswith("200"))
        self.assertIn("IP: 203.0.113.10", admin_page)
        self.assertIn("[UTC+5]", admin_page)

        status, _, _ = self.request(
            f"/support/admin/ticket/{self.ticket_id}",
            method="POST",
            fields={"action": "close"},
            cookie=cookie,
        )
        self.assertTrue(status.startswith("303"))
        status, _, _ = self.request(f"/support/t/{self.token}")
        self.assertTrue(status.startswith("404"))
        self.assertTrue(self.store.rate(self.ticket_id, 101, 5))
        self.assertFalse(self.store.rate(self.ticket_id, 101, 5))
        self.assertEqual(self.store.get(self.ticket_id)["rating"], 5)
        self.assertFalse(
            self.store.add_message(
                self.ticket_id, 101, "user", "Ответ после закрытия"
            )
        )
        self.assertFalse(self.store.close(self.ticket_id, "admin"))

    def test_admin_pages_require_valid_session_and_password(self) -> None:
        status, _, _ = self.request(
            f"/support/admin/ticket/{self.ticket_id}"
        )
        self.assertTrue(status.startswith("401"))
        status, _, _ = self.request(
            "/support/admin",
            method="POST",
            fields={"password": "wrong-password"},
        )
        self.assertTrue(status.startswith("401"))

        with self.assertRaises(ValueError):
            TicketPortalApp(
                self.store,
                admin_password="short",
                session_secret="short",
                admin_ids=(900,),
            )

    def test_ticket_history_is_paginated_without_dropping_old_messages(self) -> None:
        for index in range(55):
            self.store.add_message(
                self.ticket_id,
                101,
                "user",
                f"History message {index}",
            )

        status, headers, _ = self.request(
            "/support/admin",
            method="POST",
            fields={"password": "a-secure-test-password-long"},
        )
        self.assertTrue(status.startswith("303"))
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        status, _, latest_page = self.request(
            f"/support/admin/ticket/{self.ticket_id}", cookie=cookie
        )
        self.assertTrue(status.startswith("200"))
        self.assertIn("History message 54", latest_page)
        self.assertNotIn("<script>alert(1)</script>", latest_page)
        self.assertIn("Старые сообщения", latest_page)

        status, _, older_page = self.request(
            f"/support/admin/ticket/{self.ticket_id}?messages_page=1",
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"))
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", older_page)
        self.assertIn("Новые сообщения", older_page)
        self.assertNotIn("History message 54", older_page)


if __name__ == "__main__":
    unittest.main()
