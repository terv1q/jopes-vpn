"""Apply an authorized source archive, preserving live tariffs, secrets and volumes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path("/opt/jopesvpn")
DEPLOY = ROOT / "deploy/production"


def command(args):
    result = subprocess.run(
        args, cwd=DEPLOY, capture_output=True, text=True, check=False
    )
    if result.returncode:
        raise RuntimeError("Release command failed: " + args[0])
    return result.stdout


def apply(archive: Path, revision: str) -> dict:
    if len(revision) != 40 or any(c not in "0123456789abcdef" for c in revision):
        raise ValueError("Full source revision required")
    os.umask(0o077)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    release = ROOT / "releases" / ("service-" + stamp)
    release.mkdir(parents=True, mode=0o700)
    stage = release / "staging"
    stage.mkdir(mode=0o700)
    with tarfile.open(archive) as bundle:
        bundle.extractall(stage, filter="data")
    files = [path for path in stage.rglob("*") if path.is_file()]
    if not (stage / "jopesvpn/bot.py").is_file():
        raise ValueError("Incomplete source archive")
    preserved = [
        "deploy/production/.env",
        "deploy/production/.env.node",
        "jopesvpn/plans.json",
    ]
    old_hashes = {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in preserved
    }
    backup = release / "source-before.tar.gz"
    with tarfile.open(backup, "w:gz") as bundle:
        for path in files:
            live = ROOT / path.relative_to(stage)
            if live.is_file():
                bundle.add(live, arcname=live.relative_to(ROOT), recursive=False)
    for image in ("jopesvpn-bot", "jopesvpn-ticket-portal"):
        command(
            ["docker", "image", "tag", image + ":1.0.0", image + ":rollback-" + stamp]
        )
    for path in files:
        name = str(path.relative_to(stage))
        if name in preserved or Path(name).name in (".env", ".env.node"):
            continue
        destination = ROOT / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
        destination.chmod(0o755 if destination.suffix == ".sh" else 0o644)
    env_path = DEPLOY / ".env"
    env_text = env_path.read_text()
    values = dict(
        line.split("=", 1)
        for line in env_text.splitlines()
        if "=" in line and not line.startswith("#")
    )
    if not values.get("BOT_OWNER_ID", "").strip():
        admins = [
            v.strip() for v in values.get("BOT_ADMIN_IDS", "").split(",") if v.strip()
        ]
        if len(admins) != 1 or not admins[0].isdigit():
            raise ValueError("Unique operational owner required")
        lines = [
            line
            for line in env_text.splitlines()
            if not line.startswith("BOT_OWNER_ID=")
        ]
        env_path.write_text("\n".join(lines) + "\nBOT_OWNER_ID=" + admins[0] + "\n")
        env_path.chmod(0o600)
    compose = [
        "docker",
        "compose",
        "--profile",
        "bot",
        "-f",
        "docker-compose.yml",
        "-f",
        "docker-compose.node.yml",
    ]
    command(compose + ["config", "--quiet"])
    for name in ("sync-hysteria2-cert.sh", "install-certificate-helper.sh"):
        command(["sh", "-n", name])
    command(compose + ["build", "bot", "ticket-forum"])
    command(compose + ["up", "-d", "--no-deps", "bot", "ticket-forum"])
    for name in ("deploy/production/.env.node", "jopesvpn/plans.json"):
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != old_hashes[name]:
            raise RuntimeError("Protected live configuration changed")
    (ROOT / "RELEASE_COMMIT").write_text(revision + "\n")
    manifest = {
        str(path.relative_to(stage)): hashlib.sha256(
            (ROOT / path.relative_to(stage)).read_bytes()
        ).hexdigest()
        for path in files
        if (ROOT / path.relative_to(stage)).is_file()
    }
    (ROOT / "RELEASE_MANIFEST.json").write_text(
        json.dumps({"commit": revision, "sha256": manifest}, indent=2)
    )
    shutil.rmtree(stage)
    archive.unlink()
    return {
        "deployed": revision,
        "rollback_source": str(backup),
        "tariffs_preserved": True,
        "node_environment_preserved": True,
        "owner_configured": True,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("revision")
    args = parser.parse_args()
    try:
        print(json.dumps(apply(args.archive, args.revision)))
    except Exception as error:  # noqa: BLE001 -- suppress expanded env and build logs
        print(
            json.dumps(
                {
                    "release_failed": type(error).__name__,
                    "detail": str(error)
                    if isinstance(error, RuntimeError)
                    else "Review protected release staging",
                }
            )
        )
        raise SystemExit(1) from None
