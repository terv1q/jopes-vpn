from __future__ import annotations

import http.server
import socket
import threading
import unittest

from jopesvpn.diagnostics import (
    check_dns,
    check_tcp,
    parse_endpoint,
    run_checks,
)


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, format: str, *args: object) -> None:
        return


class DiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_dns_and_tcp_checks_report_success(self) -> None:
        self.assertTrue(check_dns("localhost").ok)
        result = check_tcp("127.0.0.1", self.port)
        self.assertTrue(result.ok)
        self.assertEqual(result.target, f"127.0.0.1:{self.port}")

    def test_closed_port_is_reported_not_raised(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reserved:
            reserved.bind(("127.0.0.1", 0))
            port = reserved.getsockname()[1]
            result = check_tcp("127.0.0.1", port)
        self.assertFalse(result.ok)
        self.assertFalse(result.detail.startswith("HTTP"))

    def test_endpoint_parser_rejects_malformed_values(self) -> None:
        self.assertEqual(parse_endpoint("connect.example:2053"), ("connect.example", 2053))
        self.assertEqual(parse_endpoint("[::1]:443"), ("::1", 443))
        for value in ("example", "example:0", "example:70000", ":443"):
            with self.subTest(value=value):
                with self.assertRaises(Exception):
                    parse_endpoint(value)

    def test_run_checks_aggregates_without_secrets(self) -> None:
        results = run_checks(
            panel_health=f"http://127.0.0.1:{self.port}/health",
            tcp_targets=[("127.0.0.1", self.port)],
            tls_targets=[],
        )
        self.assertTrue(results)
        self.assertTrue(all(result.ok for result in results))
        self.assertTrue(all("token" not in result.detail.lower() for result in results))


if __name__ == "__main__":
    unittest.main()
