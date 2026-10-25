from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path


def connect_domain(env_file: Path) -> str:
    value = os.environ.get("CONNECT_DOMAIN")
    if value is None:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, separator, candidate = line.partition("=")
            if separator and key.strip() == "CONNECT_DOMAIN":
                value = candidate.strip().strip("\"'")
                break
    if (
        value is None
        or len(value) > 253
        or re.fullmatch(
            r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}", value
        )
        is None
        or value.endswith((".example.com", ".invalid"))
    ):
        raise ValueError("CONNECT_DOMAIN must be configured with a real DNS hostname")
    return value


def render(template: Path, env_file: Path, output: Path) -> None:
    if output.resolve() in {template.resolve(), env_file.resolve()}:
        raise ValueError("Render destination must be separate from its inputs")
    domain = connect_domain(env_file)
    rendered = template.read_text(encoding="utf-8").replace("{$CONNECT_DOMAIN}", domain)
    data = json.loads(rendered)
    if (
        data["streamSettings"]["hysteriaSettings"]["masquerade"]["url"]
        != f"https://{domain}"
    ):
        raise ValueError("Hysteria masquerade does not match CONNECT_DOMAIN")
    output.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument(
        "--template",
        type=Path,
        default=Path(__file__).with_name("hysteria2-inbound.json"),
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        render(args.template, args.env_file, args.output)
    except (OSError, ValueError, KeyError):
        raise SystemExit(
            "Could not render Hysteria configuration; check CONNECT_DOMAIN and file paths"
        ) from None
    print("Hysteria configuration rendered")
