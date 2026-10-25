"""Bootstrap restricted SSH keys and private encrypted recovery using existing Git credentials."""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from nacl.public import PublicKey, SealedBox

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = Path(__file__).resolve().parent


def command(args: list[str], **kwargs) -> str:
    result = subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", check=False, **kwargs
    )
    if result.returncode:
        raise RuntimeError("Cloud setup command failed")
    return result.stdout


def run(secret: str) -> dict:
    credential = dict(
        line.split("=", 1)
        for line in command(
            ["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n"
        ).splitlines()
        if "=" in line
    )
    token = credential["password"]
    source = command(["git", "remote", "get-url", "origin"]).strip()
    match = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$", source)
    if match is None:
        raise ValueError("GitHub source remote is required")
    owner, name = match.groups()
    recovery_name = os.environ.get("JOPES_RECOVERY_REPO", "jopesvpn-recovery")
    source_repo = owner + "/" + name
    recovery_repo = owner + "/" + recovery_name

    def api(path: str, method: str = "GET", data=None):
        request = Request(
            "https://api.github.com" + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers={
                "Authorization": "Bearer " + token,
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "JopesVPN-Operations",
            },
            method=method,
        )
        with urlopen(request, timeout=30) as response:
            payload = response.read()
            return json.loads(payload) if payload else {}

    try:
        recovery = api("/repos/" + recovery_repo)
    except HTTPError as error:
        if error.code != 404:
            raise
        recovery = api(
            "/user/repos",
            "POST",
            {
                "name": recovery_name,
                "private": True,
                "auto_init": True,
                "description": "Encrypted Jopes VPN recovery snapshots and runbook",
            },
        )
    if recovery.get("private") is not True:
        raise ValueError("Recovery repository must be private")
    files = {
        ".github/workflows/backup.yml": "github-backup.yml",
        ".github/workflows/restore-check.yml": "github-restore-check.yml",
        "RECOVERY.md": "RECOVERY.md",
    }
    files.update(
        {
            "deploy/production/" + name: name
            for name in ("cloud-backup.py", "backup-verify.py", "backup-auth.py")
        }
    )
    branch = recovery["default_branch"]
    ref = api("/repos/" + recovery_repo + "/git/ref/heads/" + branch)
    parent = ref["object"]["sha"]
    commit = api("/repos/" + recovery_repo + "/git/commits/" + parent)
    tree = []
    for destination, local in files.items():
        blob = api(
            "/repos/" + recovery_repo + "/git/blobs",
            "POST",
            {
                "content": base64.b64encode((DEPLOY / local).read_bytes()).decode(),
                "encoding": "base64",
            },
        )
        tree.append(
            {"path": destination, "mode": "100644", "type": "blob", "sha": blob["sha"]}
        )
    new_tree = api(
        "/repos/" + recovery_repo + "/git/trees",
        "POST",
        {"base_tree": commit["tree"]["sha"], "tree": tree},
    )
    new_commit = api(
        "/repos/" + recovery_repo + "/git/commits",
        "POST",
        {
            "message": "Configure encrypted recovery and isolated restore verification",
            "tree": new_tree["sha"],
            "parents": [parent],
        },
    )
    api(
        "/repos/" + recovery_repo + "/git/refs/heads/" + branch,
        "PATCH",
        {"sha": new_commit["sha"], "force": False},
    )
    ssh = dict(
        line.split(" ", 1)
        for line in command(["ssh", "-G", "jopesvpn"]).splitlines()
        if " " in line
    )
    hostname, username, port = ssh["hostname"], ssh["user"], int(ssh["port"])
    known_name = hostname if port == 22 else f"[{hostname}]:{port}"
    known_path = Path.home() / ".ssh" / "known_hosts"
    known = command(["ssh-keygen", "-F", known_name, "-f", str(known_path)])
    known_lines = [
        line for line in known.splitlines() if line and not line.startswith("#")
    ]
    if not known_lines:
        raise ValueError("Trusted server host key is required")
    directory = Path.home() / "JopesVPN-Backups" / "cloud-keys"
    directory.mkdir(parents=True, exist_ok=True)
    keys = {}
    public = {}
    for role in ("backup", "monitor"):
        path = directory / role
        if not path.exists():
            command(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(path)])
        keys[role] = path.read_text()
        public[role] = path.with_name(path.name + ".pub").read_text().strip()
    remote = """import pathlib,os,json,subprocess
data=__PUBLIC__
directory=pathlib.Path.home()/'.ssh'; directory.mkdir(mode=0o700,exist_ok=True)
path=directory/'authorized_keys'; original=path.read_text() if path.exists() else ''
for role,key in data.items():
 line='restrict,command="/usr/bin/python3 /opt/jopesvpn/deploy/production/remote-operations.py '+role+'" '+key
 if key.split()[1] not in original: original += '\n'+line+'\n'
path.write_text(original); path.chmod(0o600)
def env(container): return dict(item.split('=',1) for item in json.loads(subprocess.check_output(['docker','inspect','--format','{{json .Config.Env}}',container])))
bot=env('jopesvpn-production-bot'); proxy=env('jopesvpn-production-proxy')
owner=bot.get('BOT_OWNER_ID') or (bot['BOT_ADMIN_IDS'] if len(bot['BOT_ADMIN_IDS'].split(','))==1 else '')
if not owner: raise ValueError('Unique owner required')
print(json.dumps({'endpoints':{'Bot':{'url':'https://'+proxy['CONNECT_DOMAIN']+'/healthz','statuses':[200]},'Panel':{'url':bot['PANEL_API_URL']+'/health','statuses':[200]},'Support':{'url':'https://'+proxy['CONNECT_DOMAIN']+'/support/admin/posts','statuses':[401]}},'containers':['jopesvpn-production-'+n for n in ('bot','node','panel','db','valkey','proxy','subpage','ticket-forum')],'telegram':{'token':bot['BOT_TOKEN'],'admin_ids':[int(owner)]},'min_available_mb':500,'max_swap_percent':75,'failure_threshold':2}))
""".replace("__PUBLIC__", repr(public))
    notification = json.loads(
        command(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=10",
                "jopesvpn",
                "python3 -",
            ],
            input=remote,
        )
    )

    def set_secret(repository: str, key: str, value: str):
        public_key = api("/repos/" + repository + "/actions/secrets/public-key")
        encrypted = SealedBox(PublicKey(base64.b64decode(public_key["key"]))).encrypt(
            value.encode()
        )
        api(
            "/repos/" + repository + "/actions/secrets/" + key,
            "PUT",
            {
                "encrypted_value": base64.b64encode(encrypted).decode(),
                "key_id": public_key["key_id"],
            },
        )

    common = {
        "JOPES_SSH_HOST": hostname,
        "JOPES_SSH_USER": username,
        "JOPES_SSH_PORT": str(port),
        "JOPES_SSH_KNOWN_HOSTS": "\n".join(known_lines) + "\n",
    }
    for repository in (source_repo, recovery_repo):
        for key, value in common.items():
            set_secret(repository, key, value)
    set_secret(source_repo, "JOPES_MONITOR_CONFIG", json.dumps(notification))
    set_secret(source_repo, "JOPES_MONITOR_SSH_KEY", keys["monitor"])
    set_secret(recovery_repo, "JOPES_BACKUP_SSH_KEY", keys["backup"])
    set_secret(recovery_repo, "JOPES_BACKUP_SECRET", secret)
    api(
        "/repos/" + recovery_repo + "/actions/workflows/backup.yml/dispatches",
        "POST",
        {"ref": branch, "inputs": {"verify_postgres": "true"}},
    )
    return {
        "recovery_repo": recovery_repo,
        "recovery_private": True,
        "runbook_off_workstation": bool(
            api("/repos/" + recovery_repo + "/contents/RECOVERY.md").get("sha")
        ),
        "restricted_keys": 2,
        "owner_only": True,
        "backup_dispatched": True,
    }


if __name__ == "__main__":
    try:
        incoming = json.load(sys.stdin)
        print(json.dumps(run(incoming["backup_secret"])))
    except Exception as error:  # noqa: BLE001 -- never print secret inputs or API response bodies
        print(
            json.dumps(
                {
                    "setup_failed": type(error).__name__,
                    "http_status": getattr(error, "code", None),
                }
            )
        )
        raise SystemExit(1) from None
