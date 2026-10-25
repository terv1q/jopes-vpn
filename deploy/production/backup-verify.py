from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tarfile
import tempfile
from contextlib import closing
from pathlib import Path, PurePosixPath


def verify(path: Path) -> dict[str, object]:
    with tarfile.open(path) as archive:
        members = archive.getmembers()
        if len(members) > 128:
            raise ValueError("Too many recovery files")
        names = set()
        for member in members:
            name = PurePosixPath(member.name)
            if (
                not member.isfile()
                or name.is_absolute()
                or ".." in name.parts
                or member.name in names
            ):
                raise ValueError("Unsafe recovery archive member")
            names.add(member.name)
        manifest = json.loads(archive.extractfile("manifest.json").read())
        if manifest["format"] != "jopesvpn-recovery-v1":
            raise ValueError("Unknown recovery format")
        expected = manifest["sha256"]
        if names != set(expected) | {"manifest.json"}:
            raise ValueError("Recovery file list does not match manifest")
        for name, digest in expected.items():
            stream = archive.extractfile(name)
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
            if actual != digest:
                raise ValueError("Recovery file checksum mismatch")
        for required in (
            "bot-state.sqlite3",
            "panel.dump",
            "caddy-data.tar",
            "config/.env",
            "config/.env.node",
            "config/Caddyfile",
            "config/docker-compose.yml",
        ):
            if required not in names:
                raise ValueError("Incomplete recovery bundle")
        with tempfile.TemporaryDirectory(
            prefix="verify-", dir=path.parent
        ) as directory:
            database_path = Path(directory) / "state.sqlite3"
            database_path.write_bytes(archive.extractfile("bot-state.sqlite3").read())
            with closing(
                sqlite3.connect(database_path.as_uri() + "?mode=ro", uri=True)
            ) as db:
                if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("Restored SQLite failed integrity check")
        if archive.extractfile("panel.dump").read(5) != b"PGDMP":
            raise ValueError("Invalid PostgreSQL custom-format dump")
        with tarfile.open(
            fileobj=archive.extractfile("caddy-data.tar"), mode="r|"
        ) as caddy:
            count = 0
            for member in caddy:
                name = PurePosixPath(member.name)
                if (
                    name.is_absolute()
                    or ".." in name.parts
                    or member.issym()
                    or member.islnk()
                ):
                    raise ValueError("Unsafe Caddy archive member")
                count += member.isfile()
            if count == 0:
                raise ValueError("Caddy recovery data is empty")
        return {
            "status": "ok",
            "files": len(expected),
            "postgres_restore_verified": manifest["postgres_restore_verified"],
        }


if __name__ == "__main__":
    try:
        print(json.dumps(verify(Path(sys.argv[1]))))
    except Exception as error:  # noqa: BLE001 -- never expose backup contents
        print(f"Recovery verification failed ({type(error).__name__})", file=sys.stderr)
        raise SystemExit(1) from None
