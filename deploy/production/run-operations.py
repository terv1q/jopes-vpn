"""VPS cron: owner-only notifications. Secret configuration is never printed."""

from __future__ import annotations

import fcntl
import importlib.util
import json
import os
import subprocess
from pathlib import Path
from urllib.request import Request, urlopen


def module(name: str):
    spec = importlib.util.spec_from_file_location(
        name.replace("-", "_"), Path(__file__).with_name(name + ".py")
    )
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def bot_env() -> dict:
    result = subprocess.run(
        [
            "docker",
            "inspect",
            "--format",
            "{{json .Config.Env}}",
            "jopesvpn-production-bot",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("Bot configuration unavailable")
    return dict(value.split("=", 1) for value in json.loads(result.stdout))


def owner(env: dict) -> int:
    value = env.get("BOT_OWNER_ID", "").strip()
    admins = [
        int(item) for item in env.get("BOT_ADMIN_IDS", "").split(",") if item.strip()
    ]
    if value:
        return int(value)
    if len(admins) == 1:
        return admins[0]
    raise RuntimeError("A unique owner must be configured")


def service_events() -> dict:
    source = """import json
from jopesvpn.config import Settings
from jopesvpn.service_ops import ServiceOpsStore
from jopesvpn.service_ui import event_text
s=Settings.from_env(); store=ServiceOpsStore(s.state_db_path)
counts=store.collect(s.service_policy)
print(json.dumps({'counts':counts,'events':[dict(event,text=event_text(event)) for event in store.pending()]}))
"""
    result = subprocess.run(
        ["docker", "exec", "-i", "jopesvpn-production-bot", "python", "-"],
        input=source,
        capture_output=True,
        text=True,
        timeout=40,
        check=False,
    )
    return (
        json.loads(result.stdout)
        if not result.returncode
        else {"counts": {}, "events": []}
    )


def acknowledge_events(keys: list[str]) -> None:
    if not keys:
        return
    source = (
        "from jopesvpn.config import Settings; from jopesvpn.service_ops import ServiceOpsStore; ServiceOpsStore(Settings.from_env().state_db_path).mark_notified("
        + repr(keys)
        + ")"
    )
    result = subprocess.run(
        ["docker", "exec", "-i", "jopesvpn-production-bot", "python", "-"],
        input=source,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("Could not acknowledge service events")


def run(root: Path) -> dict:
    env = bot_env()
    recipient = owner(env)
    state_dir = root / "operations-state"
    state_dir.mkdir(mode=0o700, exist_ok=True)
    try:
        events = service_events()
    except (RuntimeError, ValueError, OSError):
        events = {"events": [], "counts": {}}
    waiting = [
        event for event in events["events"] if event["kind"] == "support_waiting"
    ]
    if waiting:
        notification = "Jopes VPN — обращения ждут ответа\n" + "\n".join(
            event["text"] for event in waiting[:15]
        )
        request = Request(
            f"https://api.telegram.org/bot{env['BOT_TOKEN']}/sendMessage",
            data=json.dumps(
                {"chat_id": recipient, "text": notification[:4000]}
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=10) as response:
            if json.load(response).get("ok"):
                acknowledge_events([event["key"] for event in waiting[:15]])
    summary_events = [
        event for event in events["events"] if event["kind"] != "support_waiting"
    ][:10]
    summary = f"Тикетов без ответа: {events['counts'].get('waiting_support', '—')}.\n"
    summary += "\n".join(event["text"] for event in summary_events)
    if summary_events:
        summary += "\nСовпадения и аномалии — основания для проверки, не доказательство нарушения."
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
    config = {
        "root": str(root),
        "local": True,
        "endpoints": {},
        "containers": names,
        "telegram": {"token": env["BOT_TOKEN"], "admin_ids": [recipient]},
        "min_available_mb": int(env.get("OPS_MIN_AVAILABLE_MB", "500")),
        "critical_available_mb": int(env.get("OPS_CRITICAL_AVAILABLE_MB", "250")),
        "max_swap_percent": int(env.get("OPS_MAX_SWAP_PERCENT", "75")),
        "max_disk_percent": int(env.get("OPS_MAX_DISK_PERCENT", "85")),
        "critical_disk_percent": int(env.get("OPS_CRITICAL_DISK_PERCENT", "95")),
        "min_disk_free_gb": int(env.get("OPS_MIN_DISK_FREE_GB", "5")),
        "critical_disk_free_gb": int(env.get("OPS_CRITICAL_DISK_FREE_GB", "2")),
        "failure_threshold": 2,
        "check_backup": True,
        "max_backup_age_minutes": int(env.get("OPS_MAX_BACKUP_AGE_MINUTES", "75")),
        "daily_summary": True,
        "summary_hour_utc": int(env.get("OPS_SUMMARY_HOUR_UTC", "7")),
        "service_summary": summary,
        "critical_labels": [
            "RAM critical",
            "Disk critical",
            "Backup",
            "VPN TCP",
            "jopesvpn-production-node",
        ],
    }
    result = module("monitor").monitor(config, state_dir / "monitor.json")
    if result["summary_sent"]:
        acknowledge_events([event["key"] for event in summary_events])
    return result


if __name__ == "__main__":
    os.umask(0o077)
    root = Path(__file__).resolve().parents[2]
    state_dir = root / "operations-state"
    state_dir.mkdir(mode=0o700, exist_ok=True)
    with (state_dir / "operations.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit(0) from None
        try:
            report = run(root)
        except Exception as error:  # noqa: BLE001 -- hide private API details
            report = {"failed": type(error).__name__}
            (state_dir / "operations-last.json").write_text(json.dumps(report))
            raise SystemExit(1) from None
        (state_dir / "operations-last.json").write_text(json.dumps(report))
        log = state_dir / "operations.log"
        if log.exists() and log.stat().st_size > 1024 * 1024:
            log.replace(state_dir / "operations.previous.log")
        with log.open("a") as stream:
            stream.write(json.dumps(report) + "\n")
        print(json.dumps(report))
