"""GitHub runner backup: encrypt, verify, then upload ciphertext only."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path


def configure_ssh(role: str) -> str:
    directory = Path.home() / ".ssh"
    directory.mkdir(mode=0o700, exist_ok=True)
    key = directory / ("jopesvpn-" + role)
    key.write_text(os.environ[f"JOPES_{role.upper()}_SSH_KEY"])
    key.chmod(0o600)
    known = directory / "jopesvpn-known-hosts"
    known.write_text(os.environ["JOPES_SSH_KNOWN_HOSTS"])
    hostname = os.environ["JOPES_SSH_HOST"]
    username = os.environ["JOPES_SSH_USER"]
    port = int(os.environ.get("JOPES_SSH_PORT", "22"))
    if (
        any(character.isspace() for character in hostname + username)
        or not 1 <= port <= 65535
    ):
        raise ValueError("Invalid SSH configuration")
    config = directory / ("jopesvpn-" + role + "-config")
    config.write_text(
        f"Host jopesvpn-cloud\n HostName {hostname}\n User {username}\n Port {port}\n IdentityFile {key}\n IdentitiesOnly yes\n BatchMode yes\n StrictHostKeyChecking yes\n UserKnownHostsFile {known}\n ConnectTimeout 15\n"
    )
    return str(config)


def ssh(config: str, command: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["ssh", "-F", config, "jopesvpn-cloud", command],
        capture_output=True,
        timeout=240,
        check=False,
    )


def bundle_verify(path: Path) -> dict:
    spec = importlib.util.spec_from_file_location(
        "backup_verify", Path(__file__).with_name("backup-verify.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.verify(path)


def authenticate(secret: bytes, path: Path) -> str:
    # Compatible with the Windows backup-auth.py format.
    key = hmac.digest(secret, b"JopesVPN backup MAC key v1", "sha256")
    digest = hmac.new(key, digestmod="sha256")
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def backup(output: Path, *, verify_postgres: bool = False) -> dict:
    os.umask(0o077)
    output.mkdir(parents=True, exist_ok=True)
    config = configure_ssh("backup")
    with tempfile.TemporaryDirectory(prefix="jopesvpn-recovery-") as directory:
        staging = Path(directory)
        result = ssh(config, "backup-verify" if verify_postgres else "backup")
        if result.returncode:
            ssh(config, "backup-failed")
            raise RuntimeError("Encrypted backup export failed")
        source = staging / "source.tar.gz"
        source.write_bytes(result.stdout)
        bundle_verify(source)
        password = staging / "key"
        password.write_text(os.environ["JOPES_BACKUP_SECRET"] + "\n")
        destination = output / (
            "recovery-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + ".tar.gz.enc"
        )
        command = [
            "openssl",
            "enc",
            "-aes-256-cbc",
            "-pbkdf2",
            "-iter",
            "600000",
            "-pass",
            "file:" + str(password),
        ]
        result = subprocess.run(
            command + ["-salt", "-in", str(source), "-out", str(destination)],
            capture_output=True,
            check=False,
        )
        if result.returncode:
            raise RuntimeError("Backup encryption failed")
        restored = staging / "restore.tar.gz"
        result = subprocess.run(
            command + ["-d", "-in", str(destination), "-out", str(restored)],
            capture_output=True,
            check=False,
        )
        if result.returncode:
            raise RuntimeError("Backup verification decryption failed")
        verification = bundle_verify(restored)
        secret = os.environ["JOPES_BACKUP_SECRET"].strip().encode()
        (output / (destination.name + ".hmac")).write_text(
            authenticate(secret, destination)
        )
        return {
            "encrypted_file": destination.name,
            "bytes": destination.stat().st_size,
            "verification": verification,
        }


def verify_download(directory: Path, *, verify_postgres: bool = False) -> dict:
    path = next(directory.rglob("*.tar.gz.enc"))
    digest = authenticate(os.environ["JOPES_BACKUP_SECRET"].strip().encode(), path)
    if not hmac.compare_digest(
        digest, path.with_name(path.name + ".hmac").read_text().strip()
    ):
        raise ValueError("Recovery HMAC mismatch")
    with tempfile.TemporaryDirectory(prefix="jopesvpn-restore-") as temporary:
        staging = Path(temporary)
        key = staging / "key"
        key.write_text(os.environ["JOPES_BACKUP_SECRET"] + "\n")
        restored = staging / "restore.tar.gz"
        result = subprocess.run(
            [
                "openssl",
                "enc",
                "-d",
                "-aes-256-cbc",
                "-pbkdf2",
                "-iter",
                "600000",
                "-pass",
                "file:" + str(key),
                "-in",
                str(path),
                "-out",
                str(restored),
            ],
            capture_output=True,
            check=False,
        )
        if result.returncode:
            raise RuntimeError("Recovery decryption failed")
        report = bundle_verify(restored)
        if verify_postgres:
            import tarfile
            import time
            import uuid

            container = "jopesvpn-restore-" + uuid.uuid4().hex
            try:
                subprocess.run(
                    [
                        "docker",
                        "run",
                        "--detach",
                        "--name",
                        container,
                        "--network",
                        "none",
                        "-e",
                        "POSTGRES_HOST_AUTH_METHOD=trust",
                        "postgres:18.4-alpine3.24",
                    ],
                    capture_output=True,
                    check=True,
                    timeout=120,
                )
                for attempt in range(30):
                    ready = subprocess.run(
                        ["docker", "exec", container, "pg_isready", "-U", "postgres"],
                        capture_output=True,
                        check=False,
                    )
                    if ready.returncode == 0:
                        break
                    time.sleep(1)
                else:
                    raise RuntimeError("Isolated PostgreSQL did not become ready")
                with tarfile.open(restored) as archive:
                    dump = archive.extractfile("panel.dump").read()
                subprocess.run(
                    [
                        "docker",
                        "exec",
                        "-i",
                        container,
                        "pg_restore",
                        "--no-owner",
                        "--single-transaction",
                        "-U",
                        "postgres",
                        "-d",
                        "postgres",
                    ],
                    input=dump,
                    capture_output=True,
                    check=True,
                    timeout=120,
                )
                check = subprocess.run(
                    [
                        "docker",
                        "exec",
                        container,
                        "psql",
                        "-U",
                        "postgres",
                        "-d",
                        "postgres",
                        "-At",
                        "-c",
                        "SELECT count(*) FROM pg_tables WHERE schemaname='public'",
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                if int(check.stdout.strip()) < 1:
                    raise RuntimeError("Isolated recovery database is empty")
                report["stored_postgres_restore_verified"] = True
            finally:
                subprocess.run(
                    ["docker", "rm", "--force", "--volumes", container],
                    capture_output=True,
                    check=False,
                )
        return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("backup", "ack", "verify"))
    parser.add_argument("directory", type=Path)
    parser.add_argument("--verify-postgres", action="store_true")
    args = parser.parse_args()
    try:
        if args.action == "backup":
            print(
                json.dumps(backup(args.directory, verify_postgres=args.verify_postgres))
            )
        elif args.action == "verify":
            print(
                json.dumps(
                    verify_download(
                        args.directory, verify_postgres=args.verify_postgres
                    )
                )
            )
        else:
            path = next(args.directory.glob("*.tar.gz.enc"))
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            result = ssh(configure_ssh("backup"), "backup-ack " + digest)
            if result.returncode:
                raise RuntimeError("Off-host upload acknowledgement failed")
            print("Off-host encrypted backup acknowledged")
    except Exception as error:  # noqa: BLE001 -- prevent credential disclosure
        print(f"Cloud backup failed ({type(error).__name__})", file=sys.stderr)
        raise SystemExit(1) from None
