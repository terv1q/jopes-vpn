"""Install a bounded, owner-only VPS monitor under the existing unprivileged user."""

import os
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[2]
if root != Path("/opt/jopesvpn"):
    raise SystemExit("Production path required")
os.umask(0o077)
directory = root / "operations-state"
directory.mkdir(mode=0o700, exist_ok=True)
result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=False)
if result.returncode not in (0, 1):
    raise SystemExit("Cannot read user crontab")
lines = [
    line
    for line in result.stdout.splitlines()
    if "# JopesVPN owner operations" not in line
]
lines.append(
    "*/5 * * * * /usr/bin/python3 /opt/jopesvpn/deploy/production/run-operations.py >/dev/null 2>&1 # JopesVPN owner operations"
)
lines = [line for line in lines if "# JopesVPN expired probes" not in line]
lines.append(
    "*/10 * * * * /usr/bin/python3 /opt/jopesvpn/deploy/production/production-vpn-check.py --cleanup-only >/dev/null 2>&1 # JopesVPN expired probes"
)
subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", text=True, check=True)
print("VPS user monitoring installed; existing cron entries preserved")
