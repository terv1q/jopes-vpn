"""Forced SSH command for dedicated cloud keys. No shell, no arbitrary commands."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path


def module(name):
    spec = importlib.util.spec_from_file_location(
        name.replace("-", "_"), Path(__file__).with_name(name + ".py")
    )
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def dispatch(role: str, command: str) -> None:
    root = Path(__file__).resolve().parents[2]
    state = root / "operations-state"
    state.mkdir(mode=0o700, exist_ok=True)
    if role == "monitor" and command == "health":
        names = [
            "jopesvpn-production-" + name
            for name in (
                "bot",
                "node",
                "panel",
                "db",
                "valkey",
                "proxy",
                "subpage",
                "ticket-forum",
            )
        ]
        print(
            json.dumps(
                module("monitor").remote_health(
                    {"local": True, "containers": names, "root": str(root)}
                )
            )
        )
    elif role == "backup" and command in {"backup", "backup-verify"}:
        module("backup-export").export(root, verify_restore=command == "backup-verify")
    elif role == "backup" and re.fullmatch(r"backup-ack [0-9a-f]{64}", command):
        temporary = state / "backup.partial"
        temporary.write_text(
            json.dumps(
                {
                    "uploaded_at": time.time(),
                    "sha256": command.split()[1],
                    "failed": False,
                }
            )
        )
        temporary.replace(state / "backup.json")
        print("Backup acknowledged")
    elif role == "backup" and command == "backup-failed":
        path = state / "backup.json"
        current = json.loads(path.read_text()) if path.exists() else {}
        current["failed"] = True
        path.write_text(json.dumps(current))
        print("Backup failure recorded")
    else:
        raise ValueError("Restricted operations command rejected")


if __name__ == "__main__":
    os.umask(0o077)
    try:
        dispatch(sys.argv[1], os.environ.get("SSH_ORIGINAL_COMMAND", ""))
    except Exception as error:  # noqa: BLE001 -- never expose secrets
        print(f"Remote operation failed ({type(error).__name__})", file=sys.stderr)
        raise SystemExit(1) from None
