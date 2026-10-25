"""Stream a protected recovery bundle over SSH; never print configuration values."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


def run(args: list[str], *, input: bytes | None = None) -> bytes:
    result = subprocess.run(args, input=input, capture_output=True, check=False)
    if result.returncode:
        command = " ".join(args)
        stage = next(
            (
                name
                for name in ("pg_dump", "createdb", "pg_restore", "dropdb", "tar")
                if name in command
            ),
            args[1],
        )
        raise RuntimeError(
            f"Recovery export step failed: {stage} (exit {result.returncode})"
        )
    return result.stdout


def export(root: Path, *, verify_restore: bool = False) -> None:
    os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix="jopesvpn-backup-") as directory:
        staging = Path(directory)
        temporary_db = f"/tmp/jopesvpn-backup-{uuid4().hex}.sqlite3"
        sqlite_code = (
            "import sqlite3; "
            "source=sqlite3.connect('file:/app/data/bot-state.sqlite3?mode=ro',uri=True); "
            f"target=sqlite3.connect({temporary_db!r}); source.backup(target); "
            "assert target.execute('PRAGMA integrity_check').fetchone()[0]=='ok'; "
            "target.close(); source.close()"
        )
        try:
            run(
                [
                    "docker",
                    "exec",
                    "jopesvpn-production-bot",
                    "python",
                    "-c",
                    sqlite_code,
                ]
            )
            (staging / "bot-state.sqlite3").write_bytes(
                run(["docker", "exec", "jopesvpn-production-bot", "cat", temporary_db])
            )
        finally:
            run(["docker", "exec", "jopesvpn-production-bot", "rm", "-f", temporary_db])

        dump = run(
            [
                "docker",
                "exec",
                "jopesvpn-production-db",
                "sh",
                "-c",
                'pg_dump -Fc --no-owner -U "$POSTGRES_USER" -d "$POSTGRES_DB"',
            ]
        )
        (staging / "panel.dump").write_bytes(dump)
        run(
            ["docker", "exec", "-i", "jopesvpn-production-db", "pg_restore", "--list"],
            input=dump,
        )
        if verify_restore:
            # Only a newly created, random database owned by this run may be dropped.
            database = "jopesvpn_verify_" + uuid4().hex
            created = False
            try:
                run(
                    [
                        "docker",
                        "exec",
                        "jopesvpn-production-db",
                        "sh",
                        "-c",
                        'createdb -U "$POSTGRES_USER" "$1"',
                        "verify",
                        database,
                    ]
                )
                created = True
                run(
                    [
                        "docker",
                        "exec",
                        "-i",
                        "jopesvpn-production-db",
                        "sh",
                        "-c",
                        'pg_restore --no-owner --single-transaction -U "$POSTGRES_USER" -d "$1"',
                        "verify",
                        database,
                    ],
                    input=dump,
                )
                tables = run(
                    [
                        "docker",
                        "exec",
                        "jopesvpn-production-db",
                        "sh",
                        "-c",
                        (
                            'psql -U "$POSTGRES_USER" -d "$1" -At -c '
                            "\"SELECT count(*) FROM pg_tables WHERE schemaname='public'\""
                        ),
                        "verify",
                        database,
                    ]
                )
                if int(tables.strip()) == 0:
                    raise RuntimeError("Restored Panel database has no tables")
            finally:
                if created:
                    run(
                        [
                            "docker",
                            "exec",
                            "jopesvpn-production-db",
                            "sh",
                            "-c",
                            'dropdb -U "$POSTGRES_USER" "$1"',
                            "verify",
                            database,
                        ]
                    )

        (staging / "caddy-data.tar").write_bytes(
            run(
                [
                    "docker",
                    "exec",
                    "jopesvpn-production-proxy",
                    "tar",
                    "-C",
                    "/data",
                    "-cf",
                    "-",
                    ".",
                ]
            )
        )
        deploy = root / "deploy" / "production"
        for name in (
            ".env",
            ".env.node",
            "Caddyfile",
            "docker-compose.yml",
            "docker-compose.node.yml",
            "hysteria2-inbound.json",
            "sync-hysteria2-cert.sh",
            "jopesvpn-hysteria2-cert-sync.service",
            "jopesvpn-hysteria2-cert-sync.timer",
        ):
            source = deploy / name
            if source.is_file():
                target = staging / "config" / name
                target.parent.mkdir(exist_ok=True)
                target.write_bytes(source.read_bytes())
        for required in (".env", ".env.node", "Caddyfile", "docker-compose.yml"):
            if not (staging / "config" / required).is_file():
                raise RuntimeError("Required recovery configuration is missing")
        files = sorted(path for path in staging.rglob("*") if path.is_file())
        manifest = {
            "format": "jopesvpn-recovery-v1",
            "created_at": datetime.now(UTC).isoformat(),
            "postgres_restore_verified": verify_restore,
            "sha256": {
                str(path.relative_to(staging)).replace("\\", "/"): hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
                for path in files
            },
        }
        manifest_data = json.dumps(manifest).encode()
        with tarfile.open(fileobj=sys.stdout.buffer, mode="w|gz") as archive:
            info = tarfile.TarInfo("manifest.json")
            info.size = len(manifest_data)
            info.mode = 0o600
            archive.addfile(info, io.BytesIO(manifest_data))
            for path in files:
                archive.add(path, arcname=path.relative_to(staging), recursive=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("/opt/jopesvpn"))
    parser.add_argument("--verify-postgres-restore", action="store_true")
    args = parser.parse_args()
    try:
        export(args.root, verify_restore=args.verify_postgres_restore)
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
    except Exception as error:  # noqa: BLE001 -- keep unexpected errors free of secrets
        print(f"Recovery export failed ({type(error).__name__})", file=sys.stderr)
        raise SystemExit(1) from None
