from __future__ import annotations

import http.server
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any, BinaryIO


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROFILE_DIR = PROJECT_ROOT / "config" / "remnawave"
PINNED_XRAY_VERSION = "Xray 26.3.27"


def _find_xray() -> str | None:
    configured = os.environ.get("XRAY_BINARY")
    if configured:
        return configured
    return shutil.which("xray")


XRAY = _find_xray()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _xray_output(binary: str, *args: str) -> str:
    result = subprocess.run(
        [binary, *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout + result.stderr


def _reality_keypair(binary: str) -> tuple[str, str]:
    output = _xray_output(binary, "x25519")
    fields = dict(
        line.split(": ", 1)
        for line in output.splitlines()
        if ": " in line
    )
    return fields["PrivateKey"], fields["Password (PublicKey)"]


def _uuid(binary: str) -> str:
    return _xray_output(binary, "uuid").strip()


def _write_config(path: Path, config: dict[str, Any]) -> None:
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")


def _replace_values(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {
            key: _replace_values(child, replacements)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_replace_values(child, replacements) for child in value]
    if isinstance(value, str):
        for placeholder, replacement in replacements.items():
            value = value.replace(placeholder, replacement)
    return value


def _load_profile(name: str, replacements: dict[str, str]) -> dict[str, Any]:
    path = PROFILE_DIR / name
    with path.open(encoding="utf-8") as profile_file:
        profile = json.load(profile_file)
    return _replace_values(profile, replacements)


class _XrayProcess:
    def __init__(self, binary: str, config_path: Path, log_path: Path):
        self.binary = binary
        self.config_path = config_path
        self.log_path = log_path
        self.process: subprocess.Popen[bytes] | None = None
        self._log_file: BinaryIO | None = None

    def start(self) -> None:
        _xray_output(self.binary, "run", "-test", "-c", str(self.config_path))
        self._log_file = self.log_path.open("wb")
        self.process = subprocess.Popen(
            [self.binary, "run", "-c", str(self.config_path)],
            stdout=self._log_file,
            stderr=subprocess.STDOUT,
        )
        for _ in range(5):
            if self.process.poll() is not None:
                raise RuntimeError(
                    f"Xray exited during startup; see {self.log_path.name}"
                )
            time.sleep(0.05)

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if self._log_file is not None:
            self._log_file.close()


class _ProbeHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b"jopes-bridge-exit-lab-ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


class _ProbeServer:
    def __init__(self) -> None:
        self.server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), _ProbeHandler
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def _read_exact(connection: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        part = connection.recv(size - len(data))
        if not part:
            raise ConnectionError("SOCKS5 proxy closed the connection")
        data.extend(part)
    return bytes(data)


def _request_through_socks(
    proxy_port: int, destination_port: int, timeout: float = 10
) -> bytes:
    with socket.create_connection(
        ("127.0.0.1", proxy_port), timeout=timeout
    ) as connection:
        connection.settimeout(timeout)
        connection.sendall(b"\x05\x01\x00")
        if _read_exact(connection, 2) != b"\x05\x00":
            raise ConnectionError("SOCKS5 proxy did not accept no-auth mode")

        destination = socket.inet_aton("127.0.0.1")
        connection.sendall(
            b"\x05\x01\x00\x01"
            + destination
            + destination_port.to_bytes(2, "big")
        )
        response = _read_exact(connection, 4)
        if response[:2] != b"\x05\x00":
            raise ConnectionError(f"SOCKS5 CONNECT failed with code {response[1]}")
        address_size = {1: 4, 4: 16}.get(response[3])
        if address_size is None:
            address_size = _read_exact(connection, 1)[0]
        _read_exact(connection, address_size + 2)

        connection.sendall(
            b"GET /probe HTTP/1.1\r\nHost: lab.invalid\r\nConnection: close\r\n\r\n"
        )
        chunks = []
        while chunk := connection.recv(4096):
            chunks.append(chunk)
        return b"".join(chunks)


@unittest.skipUnless(XRAY, "Set XRAY_BINARY or put xray on PATH to run the lab")
class BridgeExitLabTests(unittest.TestCase):
    def test_vless_reality_traffic_reaches_exit(self) -> None:
        assert XRAY is not None
        self.assertIn(PINNED_XRAY_VERSION, _xray_output(XRAY, "version"))

        with tempfile.TemporaryDirectory(prefix="jopes-bridge-exit-") as temp:
            root = Path(temp)
            exit_port, bridge_port, client_port = (
                _free_port(),
                _free_port(),
                _free_port(),
            )
            probe = _ProbeServer()

            exit_user = _uuid(XRAY)
            bridge_user = _uuid(XRAY)
            exit_private, exit_public = _reality_keypair(XRAY)
            bridge_private, bridge_public = _reality_keypair(XRAY)
            exit_short_id = os.urandom(8).hex()
            bridge_short_id = os.urandom(8).hex()
            reality_target = "www.example.com:443"
            reality_sni = "www.example.com"

            exit_config = _load_profile(
                "exit-profile.json",
                {
                    "REPLACE_WITH_EXIT_REALITY_TARGET:443": reality_target,
                    "REPLACE_WITH_EXIT_SERVER_NAME": reality_sni,
                    "REPLACE_WITH_EXIT_REALITY_PRIVATE_KEY": exit_private,
                    "REPLACE_WITH_EXIT_SHORT_ID": exit_short_id,
                },
            )
            exit_inbound = exit_config["inbounds"][0]
            exit_inbound["listen"] = "127.0.0.1"
            exit_inbound["port"] = exit_port
            exit_inbound["settings"]["clients"] = [{"id": exit_user}]
            exit_config["log"]["access"] = str(root / "exit-access.log")

            bridge_config = _load_profile(
                "bridge-profile.json",
                {
                    "REPLACE_WITH_BRIDGE_REALITY_TARGET:443": reality_target,
                    "REPLACE_WITH_BRIDGE_SERVER_NAME": reality_sni,
                    "REPLACE_WITH_BRIDGE_REALITY_PRIVATE_KEY": bridge_private,
                    "REPLACE_WITH_BRIDGE_SHORT_ID": bridge_short_id,
                    "REPLACE_WITH_EXIT_ADDRESS": "127.0.0.1",
                    "REPLACE_WITH_EXIT_SERVICE_USER_UUID": exit_user,
                    "REPLACE_WITH_EXIT_SERVER_NAME": reality_sni,
                    "REPLACE_WITH_EXIT_REALITY_PUBLIC_KEY": exit_public,
                    "REPLACE_WITH_EXIT_SHORT_ID": exit_short_id,
                },
            )
            bridge_inbound = bridge_config["inbounds"][0]
            bridge_inbound["listen"] = "127.0.0.1"
            bridge_inbound["port"] = bridge_port
            bridge_inbound["settings"]["clients"] = [{"id": bridge_user}]
            bridge_config["outbounds"][1]["settings"]["vnext"][0]["port"] = exit_port
            bridge_config["log"]["access"] = str(root / "bridge-access.log")

            client_config = {
                "inbounds": [
                    {
                        "tag": "local-socks",
                        "listen": "127.0.0.1",
                        "port": client_port,
                        "protocol": "socks",
                        "settings": {"auth": "noauth", "udp": False},
                    }
                ],
                "outbounds": [
                    {
                        "tag": "to-bridge",
                        "protocol": "vless",
                        "settings": {
                            "vnext": [
                                {
                                    "address": "127.0.0.1",
                                    "port": bridge_port,
                                    "users": [
                                        {"id": bridge_user, "encryption": "none"}
                                    ],
                                }
                            ]
                        },
                        "streamSettings": {
                            "network": "raw",
                            "security": "reality",
                            "realitySettings": {
                                "serverName": reality_sni,
                                "fingerprint": "chrome",
                                "password": bridge_public,
                                "shortId": bridge_short_id,
                            },
                        },
                    }
                ],
            }

            configs = [
                (root / "exit.json", exit_config),
                (root / "bridge.json", bridge_config),
                (root / "client.json", client_config),
            ]
            for path, config in configs:
                _write_config(path, config)

            processes = [
                _XrayProcess(
                    XRAY, root / "exit.json", root / "exit.log"
                ),
                _XrayProcess(
                    XRAY, root / "bridge.json", root / "bridge.log"
                ),
                _XrayProcess(
                    XRAY, root / "client.json", root / "client.log"
                ),
            ]
            started: list[_XrayProcess] = []
            probe.start()
            try:
                for process in processes:
                    started.append(process)
                    process.start()

                response = _request_through_socks(client_port, probe.port)
                self.assertIn(b"200 OK", response)
                self.assertIn(b"jopes-bridge-exit-lab-ok", response)

                exit_log = root / "exit-access.log"
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    if exit_log.exists() and str(probe.port) in exit_log.read_text(
                        encoding="utf-8", errors="replace"
                    ):
                        break
                    time.sleep(0.05)
                self.assertTrue(
                    exit_log.exists()
                    and str(probe.port)
                    in exit_log.read_text(encoding="utf-8", errors="replace"),
                    "The probe request did not appear in the exit access log",
                )

                exit_process, bridge_process = processes[:2]
                exit_process.stop()
                try:
                    failed_response = _request_through_socks(
                        client_port, probe.port, timeout=2
                    )
                except (ConnectionError, TimeoutError):
                    failed_response = b""
                self.assertNotIn(b"200 OK", failed_response)
                self.assertNotIn(b"jopes-bridge-exit-lab-ok", failed_response)
                exit_process.start()
                response = _request_through_socks(client_port, probe.port)
                self.assertIn(b"200 OK", response)

                bridge_process.stop()
                try:
                    failed_response = _request_through_socks(
                        client_port, probe.port, timeout=2
                    )
                except (ConnectionError, TimeoutError):
                    failed_response = b""
                self.assertNotIn(b"200 OK", failed_response)
                self.assertNotIn(b"jopes-bridge-exit-lab-ok", failed_response)
                bridge_process.start()
                response = _request_through_socks(client_port, probe.port)
                self.assertIn(b"200 OK", response)
                self.assertIn(b"jopes-bridge-exit-lab-ok", response)
            finally:
                for process in reversed(started):
                    process.stop()
                probe.stop()


if __name__ == "__main__":
    unittest.main()
