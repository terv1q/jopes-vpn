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
PROFILE_PATH = (
    PROJECT_ROOT
    / "deploy"
    / "production"
    / "remnawave"
    / "germany-exit-profile-resilient.json"
)
MODES = ("packet-up", "stream-up", "stream-one", "auto")


def _find_xray() -> str | None:
    return os.environ.get("XRAY_BINARY") or shutil.which("xray")


XRAY = _find_xray()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _run(binary: str, *args: str) -> str:
    result = subprocess.run(
        [binary, *args], check=False, capture_output=True, text=True, timeout=10
    )
    if result.returncode != 0:
        raise RuntimeError(
            (result.stderr or result.stdout).strip()
            or f"command failed with {result.returncode}"
        )
    return result.stdout + result.stderr


def _reality_keypair(binary: str) -> tuple[str, str]:
    output = _run(binary, "x25519")
    fields = dict(
        line.split(": ", 1) for line in output.splitlines() if ": " in line
    )
    return fields["PrivateKey"], fields["Password (PublicKey)"]


def _uuid(binary: str) -> str:
    return _run(binary, "uuid").strip()


def _replace(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _replace(child, replacements) for key, child in value.items()}
    if isinstance(value, list):
        return [_replace(child, replacements) for child in value]
    if isinstance(value, str):
        for placeholder, replacement in replacements.items():
            value = value.replace(placeholder, replacement)
    return value


def _write(path: Path, config: dict[str, Any]) -> None:
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")


def _wait_port(port: int, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"port {port} did not open")


class _Process:
    def __init__(self, binary: str, config: Path, log: Path):
        self.binary = binary
        self.config = config
        self.log = log
        self.process: subprocess.Popen[bytes] | None = None
        self._handle: BinaryIO | None = None

    def start(self) -> None:
        _run(self.binary, "run", "-test", "-c", str(self.config))
        self._handle = self.log.open("wb")
        self.process = subprocess.Popen(
            [self.binary, "run", "-c", str(self.config)],
            stdout=self._handle,
            stderr=subprocess.STDOUT,
        )
        for _ in range(10):
            if self.process.poll() is not None:
                raise RuntimeError(f"Xray exited; see {self.log}")
            time.sleep(0.05)

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if self._handle is not None:
            self._handle.close()


class _ProbeHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b"jopes-xhttp-lab-ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


class _Probe:
    def __init__(self) -> None:
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _ProbeHandler)
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


def _request(proxy_port: int, destination_port: int, timeout: float = 10) -> bytes:
    with socket.create_connection(("127.0.0.1", proxy_port), timeout=timeout) as conn:
        conn.settimeout(timeout)
        conn.sendall(b"\x05\x01\x00")
        if _read_exact(conn, 2) != b"\x05\x00":
            raise ConnectionError("SOCKS5 no-auth rejected")
        conn.sendall(
            b"\x05\x01\x00\x01"
            + socket.inet_aton("127.0.0.1")
            + destination_port.to_bytes(2, "big")
        )
        response = _read_exact(conn, 4)
        if response[:2] != b"\x05\x00":
            raise ConnectionError(f"SOCKS5 CONNECT code {response[1]}")
        size = {1: 4, 4: 16}.get(response[3])
        if size is None:
            size = _read_exact(conn, 1)[0]
        _read_exact(conn, size + 2)
        conn.sendall(
            b"GET /probe HTTP/1.1\r\nHost: lab.invalid\r\nConnection: close\r\n\r\n"
        )
        chunks = []
        while chunk := conn.recv(4096):
            chunks.append(chunk)
        return b"".join(chunks)


@unittest.skipUnless(XRAY, "Set XRAY_BINARY or put xray on PATH to run the lab")
class XhttpRealityLabTests(unittest.TestCase):
    def test_raw_and_all_xhttp_modes_reach_server(self) -> None:
        assert XRAY is not None
        self.assertIn("Xray", _run(XRAY, "version"))

        with tempfile.TemporaryDirectory(prefix="jopes-xhttp-") as temp:
            root = Path(temp)
            raw_port, xhttp_port, client_port = _free_port(), _free_port(), _free_port()
            probe = _Probe()
            user = _uuid(XRAY)
            private_key, public_key = _reality_keypair(XRAY)
            short_id = os.urandom(8).hex()
            sni = "www.example.com"

            with PROFILE_PATH.open(encoding="utf-8") as profile_file:
                server_config = _replace(
                    json.load(profile_file),
                    {
                        "REPLACE_WITH_GERMANY_REALITY_TARGET:443": f"{sni}:443",
                        "REPLACE_WITH_GERMANY_SERVER_NAME": sni,
                        "REPLACE_WITH_GERMANY_REALITY_PRIVATE_KEY": private_key,
                        "REPLACE_WITH_GERMANY_SHORT_ID": short_id,
                    },
                )
            server_config["inbounds"][0]["listen"] = "127.0.0.1"
            server_config["inbounds"][0]["port"] = raw_port
            server_config["inbounds"][1]["listen"] = "127.0.0.1"
            server_config["inbounds"][1]["port"] = xhttp_port
            for inbound in server_config["inbounds"]:
                inbound["settings"]["clients"] = [{"id": user}]
            server_config["outbounds"][1]["settings"] = {
                "finalRules": [
                    {
                        "action": "allow",
                        "network": "tcp,udp",
                        "ip": ["geoip:private"],
                    }
                ]
            }

            server_path = root / "server.json"
            _write(server_path, server_config)
            server = _Process(XRAY, server_path, root / "server.log")

            def client_config(network: str, mode: str | None) -> dict[str, Any]:
                stream: dict[str, Any] = {
                    "network": network,
                    "security": "reality",
                    "realitySettings": {
                        "serverName": sni,
                        "fingerprint": "chrome",
                        "password": public_key,
                        "shortId": short_id,
                    },
                }
                if network == "xhttp":
                    stream["xhttpSettings"] = {
                        "path": "/jopes-xhttp",
                        "mode": mode,
                    }
                return {
                    "inbounds": [
                        {
                            "tag": "socks",
                            "listen": "127.0.0.1",
                            "port": client_port,
                            "protocol": "socks",
                            "settings": {"auth": "noauth", "udp": False},
                        }
                    ],
                    "outbounds": [
                        {
                            "tag": "proxy",
                            "protocol": "vless",
                            "settings": {
                                "vnext": [
                                    {
                                        "address": "127.0.0.1",
                                        "port": raw_port if network == "raw" else xhttp_port,
                                        "users": [{"id": user, "encryption": "none"}],
                                    }
                                ]
                            },
                            "streamSettings": stream,
                        }
                    ],
                }

            probe.start()
            server.start()
            try:
                _wait_port(raw_port)
                _wait_port(xhttp_port)

                cases: list[tuple[str, str | None]] = [("raw", None)]
                cases.extend(("xhttp", mode) for mode in MODES)
                for network, mode in cases:
                    with self.subTest(network=network, mode=mode):
                        config_path = root / f"client-{network}-{mode or 'default'}.json"
                        _write(config_path, client_config(network, mode))
                        client = _Process(
                            XRAY,
                            config_path,
                            root / f"client-{network}-{mode or 'default'}.log",
                        )
                        client.start()
                        try:
                            _wait_port(client_port)
                            response = _request(client_port, probe.port)
                        finally:
                            client.stop()
                        self.assertIn(b"200 OK", response)
                        self.assertIn(b"jopes-xhttp-lab-ok", response)
            finally:
                server.stop()
                probe.stop()


if __name__ == "__main__":
    unittest.main()
