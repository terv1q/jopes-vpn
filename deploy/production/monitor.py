from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def health_source(containers: list[str], root: str = "/opt/jopesvpn") -> str:
    return """import json,pathlib,subprocess,shutil,time
wanted=__CONTAINERS__
root=pathlib.Path(__ROOT__)
containers={}
for name in wanted:
 r=subprocess.run(['docker','inspect','--format','{{json .State}}',name],capture_output=True,text=True)
 if r.returncode: containers[name]=False; continue
 s=json.loads(r.stdout); containers[name]=s['Running'] and s.get('Health',{}).get('Status','healthy')=='healthy'
mem={line.split(':')[0]:int(line.split()[1]) for line in pathlib.Path('/proc/meminfo').read_text().splitlines()}
ports=subprocess.run(['ss','-H','-lnt'],capture_output=True,text=True).stdout
disk=shutil.disk_usage(root)
backup={}
try: backup=json.loads((root/'operations-state/backup.json').read_text())
except (FileNotFoundError,ValueError): pass
print(json.dumps({'containers':containers,'available_mb':mem['MemAvailable']//1024,'swap_percent':100*(mem['SwapTotal']-mem['SwapFree'])/max(1,mem['SwapTotal']),'vpn_tcp_ports':all(':'+str(port)+' ' in ports for port in (2053,8443)),'disk_percent':round(100*disk.used/disk.total,1),'disk_free_gb':round(disk.free/1024**3,2),'backup_age_minutes':(time.time()-backup['uploaded_at'])/60 if backup.get('uploaded_at') else None,'backup_failed':backup.get('failed',False)}))
""".replace("__CONTAINERS__", repr(containers)).replace("__ROOT__", repr(root))


def remote_health(config: dict) -> dict:
    source = health_source(config["containers"], config.get("root", "/opt/jopesvpn"))
    command = (
        ["python3", "-"]
        if config.get("local")
        else [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            config["ssh_host"],
            "python3 -",
        ]
    )
    result = subprocess.run(
        command,
        input=source,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    if result.returncode:
        raise OSError("Remote health probe failed")
    return json.loads(result.stdout)


def http_health(url: str, allowed: list[int]) -> bool:
    try:
        request = Request(url, headers={"User-Agent": "JopesVPN-Monitor/1"})
        with urlopen(request, timeout=10) as response:
            return response.status in allowed
    except HTTPError as error:
        return error.code in allowed
    except (URLError, OSError, TimeoutError):
        return False


def transitions(
    state: dict, failed: set[str], labels: set[str], threshold: int
) -> dict:
    counts = state.setdefault("counts", {})
    active = set(state.get("active", []))
    for label in labels:
        count = counts.get(label, 0)
        count = max(0, count) + 1 if label in failed else min(0, count) - 1
        counts[label] = max(-threshold, min(threshold, count))
        if count >= threshold:
            active.add(label)
        elif count <= -threshold:
            active.discard(label)
    state["active"] = sorted(active)
    return state


def notify(config: dict, state: dict) -> int:
    notification = config.get("telegram")
    if not notification:
        return 0
    critical = set(config.get("critical_labels", state["active"]))
    active = sorted(set(state["active"]) & critical)
    signature = ",".join(active) if active else "ok"
    delivered = state.setdefault("notifications", {})
    failures = 0
    for admin_id in notification["admin_ids"]:
        recipient_key = "owner" if config.get("single_owner") else str(admin_id)
        previous = delivered.get(recipient_key)
        if previous == signature or (previous is None and not active):
            continue
        text = (
            "Jopes VPN: мониторинг обнаружил проблему.\n" + "\n".join(active)
            if active
            else "Jopes VPN: все проверяемые сервисы восстановились."
        )
        request = Request(
            f"https://api.telegram.org/bot{notification['token']}/sendMessage",
            data=json.dumps({"chat_id": admin_id, "text": text}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as response:
                ok = json.load(response).get("ok", False)
        except (HTTPError, URLError, OSError, TimeoutError, ValueError):
            ok = False
        if ok:
            delivered[recipient_key] = signature
        else:
            failures += 1
    return failures


def daily_summary(config: dict, state: dict, resources: dict) -> tuple[int, bool]:
    notification = config.get("telegram")
    if not notification or not config.get("daily_summary", False):
        return 0, False
    now = datetime.now(UTC)
    day = now.date().isoformat()
    if now.hour < config.get("summary_hour_utc", 7) or state.get("summary_day") == day:
        return 0, False
    text = "Jopes VPN — сводка за сутки\n"
    text += "Проверки: " + (", ".join(state["active"]) or "сервисы доступны")
    text += f"\nRAM доступно: {resources.get('available_mb', '—')} МиБ; диск: {resources.get('disk_percent', '—')}%."
    minimum = state.get("min_available_mb")
    if minimum is not None:
        text += f"\nМинимум доступной RAM за период: {minimum} МиБ."
    age = resources.get("backup_age_minutes")
    text += (
        f"\nПоследний внешний бэкап: {round(age)} мин. назад."
        if age is not None
        else "\nВнешний бэкап ещё не подтверждён."
    )
    text += "\n" + config.get("service_summary", "")
    failures = 0
    for admin_id in notification["admin_ids"]:
        request = Request(
            f"https://api.telegram.org/bot{notification['token']}/sendMessage",
            data=json.dumps({"chat_id": admin_id, "text": text[:4000]}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as response:
                ok = json.load(response).get("ok", False)
        except (HTTPError, URLError, OSError, TimeoutError, ValueError):
            ok = False
        failures += int(not ok)
    if not failures:
        state["summary_day"] = day
        state.pop("min_available_mb", None)
    return failures, not failures


def monitor(config: dict, state_path: Path) -> dict:
    failed = set()
    labels = set(config["endpoints"]) | {"SSH"}
    for label, endpoint in config["endpoints"].items():
        if not http_health(endpoint["url"], endpoint["statuses"]):
            failed.add(label)
    resources = {}
    try:
        resources = remote_health(config)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        failed.add("SSH")
    else:
        labels |= set(config["containers"]) | {
            "RAM",
            "Swap",
            "VPN TCP",
            "Disk",
            "RAM critical",
            "Disk critical",
            "Backup",
        }
        failed.update(
            name for name, running in resources["containers"].items() if not running
        )
        if resources["available_mb"] < config["min_available_mb"]:
            failed.add("RAM")
        if resources["swap_percent"] > config["max_swap_percent"]:
            failed.add("Swap")
        if not resources["vpn_tcp_ports"]:
            failed.add("VPN TCP")
        if resources.get("disk_percent", 0) >= config.get(
            "max_disk_percent", 85
        ) or resources.get("disk_free_gb", float("inf")) < config.get(
            "min_disk_free_gb", 5
        ):
            failed.add("Disk")
        if resources["available_mb"] < config.get("critical_available_mb", 250):
            failed.add("RAM critical")
        if resources.get("disk_percent", 0) >= config.get(
            "critical_disk_percent", 95
        ) or resources.get("disk_free_gb", float("inf")) < config.get(
            "critical_disk_free_gb", 2
        ):
            failed.add("Disk critical")
        age = resources.get("backup_age_minutes")
        if config.get("check_backup") and (
            age is None
            or age > config.get("max_backup_age_minutes", 75)
            or resources.get("backup_failed")
        ):
            failed.add("Backup")
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        state = {}
    state = transitions(state, failed, labels, config["failure_threshold"])
    available = resources.get("available_mb")
    if available is not None:
        state["min_available_mb"] = min(
            state.get("min_available_mb", available), available
        )
    notification_failures = notify(config, state)
    summary_failures, summary_sent = daily_summary(config, state, resources)
    notification_failures += summary_failures
    temporary = state_path.with_suffix(".partial")
    temporary.write_text(json.dumps(state), encoding="utf-8")
    temporary.replace(state_path)
    return {
        "checked_at": datetime.now(UTC).isoformat(),
        "status": "degraded" if state["active"] else "ok",
        "issues": state["active"],
        "pending": sorted(failed - set(state["active"])),
        "available_mb": resources.get("available_mb"),
        "notification_failures": notification_failures,
        "summary_sent": summary_sent,
    }


if __name__ == "__main__":
    try:
        config = json.load(sys.stdin)
        report = monitor(config, Path(sys.argv[1]))
        print(json.dumps(report))
        raise SystemExit(
            1 if report["status"] != "ok" or report["notification_failures"] else 0
        )
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        print(f"Monitor failed ({type(error).__name__})", file=sys.stderr)
        raise SystemExit(2) from None
