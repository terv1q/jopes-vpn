"""Secret-free reachability checks.

A successful TCP connect is not proof of an open port when a local proxy or
security product intercepts outbound connections. Use TLS/HTTP checks, an
external checker, or the server's own socket table for firewall decisions.
"""

from __future__ import annotations

import argparse
import json
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

USER_AGENT = "Jopes-Diagnostics/1.0"


@dataclass(frozen=True)
class CheckResult:
    name: str
    target: str
    ok: bool
    detail: str
    latency_ms: int


def _elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


def check_dns(host: str, *, timeout: float = 5) -> CheckResult:
    started = time.monotonic()
    previous_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(timeout)
    try:
        addresses = sorted(
            {item[4][0] for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)}
        )
    except OSError as error:
        return CheckResult(
            "dns", host, False, type(error).__name__, _elapsed_ms(started)
        )
    finally:
        socket.setdefaulttimeout(previous_timeout)
    return CheckResult(
        "dns", host, bool(addresses), ",".join(addresses), _elapsed_ms(started)
    )


def check_tcp(host: str, port: int, *, timeout: float = 5) -> CheckResult:
    started = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except OSError as error:
        return CheckResult(
            "tcp",
            f"{host}:{port}",
            False,
            type(error).__name__,
            _elapsed_ms(started),
        )
    return CheckResult(
        "tcp", f"{host}:{port}", True, "connected", _elapsed_ms(started)
    )


def check_tls(host: str, port: int, *, timeout: float = 5) -> CheckResult:
    started = time.monotonic()
    context = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname=host) as tls:
                certificate = tls.getpeercert()
    except (OSError, ssl.SSLError) as error:
        return CheckResult(
            "tls",
            f"{host}:{port}",
            False,
            type(error).__name__,
            _elapsed_ms(started),
        )

    expires = certificate.get("notAfter")
    if not expires:
        return CheckResult(
            "tls", f"{host}:{port}", False, "certificate has no expiry", _elapsed_ms(started)
        )
    try:
        expires_at = datetime.fromtimestamp(
            ssl.cert_time_to_seconds(expires), tz=timezone.utc
        )
    except ValueError:
        return CheckResult(
            "tls", f"{host}:{port}", False, "invalid certificate expiry", _elapsed_ms(started)
        )
    days_left = (expires_at - datetime.now(tz=timezone.utc)).days
    return CheckResult(
        "tls",
        f"{host}:{port}",
        True,
        f"expires {expires_at.date().isoformat()} ({days_left} days)",
        _elapsed_ms(started),
    )


def check_https(url: str, *, timeout: float = 5) -> CheckResult:
    started = time.monotonic()
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
            final_url = response.geturl()
    except urllib.error.HTTPError as error:
        return CheckResult(
            "https", url, False, f"HTTP {error.code}", _elapsed_ms(started)
        )
    except (OSError, urllib.error.URLError) as error:
        return CheckResult(
            "https", url, False, type(error).__name__, _elapsed_ms(started)
        )
    return CheckResult(
        "https",
        url,
        status == 200,
        f"HTTP {status} {final_url}",
        _elapsed_ms(started),
    )


def parse_endpoint(value: str) -> tuple[str, int]:
    host, separator, raw_port = value.rpartition(":")
    if not separator or not host:
        raise argparse.ArgumentTypeError("endpoint must be HOST:PORT")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    try:
        port = int(raw_port)
    except ValueError as error:
        raise argparse.ArgumentTypeError("endpoint port must be an integer") from error
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("endpoint port must be 1-65535")
    return host, port


def run_checks(
    *,
    panel_health: str,
    tcp_targets: list[tuple[str, int]],
    tls_targets: list[tuple[str, int]],
    timeout: float = 5,
) -> list[CheckResult]:
    results: list[CheckResult] = []
    for host, port in tcp_targets:
        results.append(check_dns(host, timeout=timeout))
        results.append(check_tcp(host, port, timeout=timeout))
    for host, port in tls_targets:
        results.append(check_dns(host, timeout=timeout))
        results.append(check_tls(host, port, timeout=timeout))
    results.append(check_https(panel_health, timeout=timeout))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Zero-dependency Jopes VPN reachability checks; prints no secrets."
    )
    parser.add_argument(
        "--panel-health",
        default="https://remnawave.example.com/health",
        help="Panel health URL",
    )
    parser.add_argument(
        "--tcp",
        action="append",
        type=parse_endpoint,
        default=[],
        metavar="HOST:PORT",
        help="TCP endpoint to probe; repeatable",
    )
    parser.add_argument(
        "--tls",
        action="append",
        type=parse_endpoint,
        default=[],
        metavar="HOST:PORT",
        help="TLS endpoint to probe with certificate expiry; repeatable",
    )
    parser.add_argument("--timeout", type=float, default=5)
    parser.add_argument("--json", action="store_true", help="emit JSON only")
    args = parser.parse_args(argv)

    tcp_targets = args.tcp or [("connect.example.com", 2053)]
    tls_targets = args.tls or [("remnawave.example.com", 443)]
    results = run_checks(
        panel_health=args.panel_health,
        tcp_targets=tcp_targets,
        tls_targets=tls_targets,
        timeout=args.timeout,
    )

    if args.json:
        print(json.dumps([asdict(result) for result in results], indent=2))
    else:
        for result in results:
            state = "OK" if result.ok else "FAIL"
            print(
                f"{state:4} {result.name:5} {result.target} "
                f"{result.detail} ({result.latency_ms} ms)"
            )
    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
